import json
from importlib import resources
from pathlib import Path

from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name


def _removed_records(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    records = payload.get("channels", {})
    return records if isinstance(records, dict) else {}


def fetch_channels(removed_file: Path | None = None) -> list[Channel]:
    with resources.files("legal_iptv.resources").joinpath("extra_channels.json").open("r", encoding="utf-8") as file:
        raw = json.load(file)

    removed_records = _removed_records(removed_file)
    channels: list[Channel] = []
    for item in raw:
        removed_record = removed_records.get(item["id"], {})
        removed = (
            isinstance(removed_record, dict)
            and removed_record.get("url") == item["url"]
        )
        channels.append(Channel(
            id=item["id"],
            name=item["name"],
            stream_url=item["url"],
            logo=item.get("logo", ""),
            group=localized_category_name(item.get("group", "general")),
            source="extra",
            tvg_id=item.get("tvg_id"),
            provider_id="extra",
            logical_channel_id=item.get("logical_channel_id") or item["id"],
            variant_id=item.get("variant_id") or item["id"],
            protocol=item.get("protocol"),
            raw_group=str(item.get("group", "general")),
            removed=removed,
            status="removed" if removed else None,
            removal_reason=(
                str(removed_record.get("reason"))
                if removed and removed_record.get("reason")
                else None
            ),
        ))
    return channels
