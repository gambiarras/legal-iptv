import json
from pathlib import Path

from legal_iptv.clients import HttpClient
from legal_iptv.local_config import load_resource
from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name
from legal_iptv.sources import curated_text


def _removed_records(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    records = payload.get("channels", {})
    return records if isinstance(records, dict) else {}


def fetch_channels(removed_file: Path | None = None, *, include_removed: bool = False,
                   client: HttpClient | None = None, cache_only: bool = False,
                   cache_dir: Path | None = None) -> list[Channel]:
    raw = load_resource("extra_channels.json")

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
            feed_id=item.get("feed_id"),
            tags=[str(value) for value in item.get("tags", [])] if isinstance(item.get("tags"), list) else [],
            provider_id="extra",
            logical_channel_id=item.get("logical_channel_id") or item["id"],
            variant_id=item.get("variant_id") or item["id"],
            protocol=item.get("protocol"),
            raw_group=str(item.get("group", "general")),
            removed=removed and not include_removed,
            status="removed" if removed and not include_removed else None,
            removal_reason=(
                str(removed_record.get("reason"))
                if removed and removed_record.get("reason")
                else None
            ),
        ))
    channels.extend(curated_text.fetch_channels(
        client, cache_dir=cache_dir or (removed_file.parent if removed_file is not None else Path.cwd()) / ".cache" / "curated-text",
        cache_only=cache_only))
    return channels
