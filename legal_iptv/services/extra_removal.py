import json
from datetime import datetime, timezone
from pathlib import Path

from legal_iptv.io import write_json_atomic
from legal_iptv.models import Channel


TERMINAL_HTTP_STATUSES = {404, 410}


def update_extra_removals(
    channels: list[Channel],
    http_status_by_url: dict[str, int | None],
    output_path: Path,
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
        channel.id: channel
        for channel in channels
        if channel.source == "extra"
    }
    records: dict[str, dict] = {}
    for channel_id, record in existing.items():
        channel = configured_by_id.get(channel_id)
        if not channel or not isinstance(record, dict):
            continue
        if record.get("url") == channel.stream_url:
            records[channel_id] = record

    now = datetime.now(timezone.utc).isoformat()
    for channel in configured_by_id.values():
        status = http_status_by_url.get(channel.stream_url)
        if status in TERMINAL_HTTP_STATUSES:
            records[channel.id] = {
                "url": channel.stream_url,
                "removed_at": now,
                "reason": f"http_{status}",
            }
        elif status is not None and 200 <= status < 400:
            records.pop(channel.id, None)

    write_json_atomic(
        output_path,
        {
            "updated_at": now,
            "channels": {
                channel_id: records[channel_id]
                for channel_id in sorted(records)
            },
        },
    )
