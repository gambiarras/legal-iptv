import json
from datetime import datetime, timezone
from pathlib import Path

from legal_iptv.io import write_json_atomic
from legal_iptv.models import Channel
from legal_iptv.models.stream_validation import UrlValidation

TERMINAL_HTTP_STATUSES = {404, 410}


def update_extra_removals(
    channels: list[Channel],
    http_status_by_url: dict[str, int | None],
    output_path: Path,
    *,
    http_status_by_channel: dict[str, int | None] | None = None,
    validation_by_channel: dict[str, UrlValidation] | None = None,
) -> None:
    existing: dict[str, dict] = {}
    if output_path.exists():
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(payload.get("channels"), dict):
                existing = payload["channels"]
        except (OSError, json.JSONDecodeError):
            existing = {}

    configured_by_id = {
        channel.id: channel for channel in channels if channel.source == "extra"
    }
    records: dict[str, dict] = {}
    for channel_id, record in existing.items():
        channel = configured_by_id.get(channel_id)
        if not isinstance(record, dict):
            continue
        # An omitted/filtered channel is not evidence its tombstone recovered.
        if channel is None or record.get("url") == channel.stream_url:
            records[channel_id] = record

    now = datetime.now(timezone.utc).isoformat()
    for channel in configured_by_id.values():
        observed = (
            validation_by_channel.get(channel.id)
            if validation_by_channel is not None
            else None
        )
        if validation_by_channel is not None:
            status = observed.http_status if observed is not None else None
        elif http_status_by_channel is not None:
            status = http_status_by_channel.get(channel.id)
        else:
            status = http_status_by_url.get(channel.stream_url)
        if status in TERMINAL_HTTP_STATUSES and (
            validation_by_channel is None
            or (
                observed is not None
                and observed.active is False
                and observed.error_type in {None, "HttpNotFound"}
            )
        ):
            records[channel.id] = {
                "url": channel.stream_url,
                "removed_at": now,
                "reason": f"http_{status}",
            }
        elif observed is not None and observed.active is True:
            records.pop(channel.id, None)

    if len(records) > 5000:
        raise ValueError("Extra removal record budget exceeded")

    write_json_atomic(
        output_path,
        {
            "updated_at": now,
            "channels": {
                channel_id: records[channel_id] for channel_id in sorted(records)
            },
        },
    )
