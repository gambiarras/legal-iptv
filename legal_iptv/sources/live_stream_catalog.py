import json
from datetime import datetime, timezone
from pathlib import Path

from legal_iptv.clients import HttpClient
from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name


LIVE_STREAM_CATALOG_URL = "https://raw.githubusercontent.com/gambiarras/live-stream-catalog/main/channels.json"
TTL_FILTER_EXEMPT_SOURCE_TYPES = {"kick"}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def _current_ttl_seconds(item: dict) -> int | None:
    expires_at = _parse_datetime(item.get("expires_at"))
    if expires_at is not None:
        return max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))

    return item.get("ttl_seconds")


def _should_filter_by_ttl(item: dict) -> bool:
    return item.get("source_type") not in TTL_FILTER_EXEMPT_SOURCE_TYPES


def _is_usable(item: dict, min_live_ttl: int) -> bool:
    if item.get("status") != "resolved":
        return False

    stream_url = item.get("stream_url") or item.get("url")
    if not stream_url:
        return False

    ttl_seconds = _current_ttl_seconds(item)
    if _should_filter_by_ttl(item) and ttl_seconds is not None and ttl_seconds < min_live_ttl:
        return False

    return True


def _load_raw(client: HttpClient, local_file: Path | None) -> list[dict]:
    if local_file is not None:
        if not local_file.exists():
            raise FileNotFoundError(f"Live catalog file not found: {local_file}")

        return json.loads(local_file.read_text(encoding="utf-8"))

    return client.get_json(LIVE_STREAM_CATALOG_URL)


def fetch_channels(
    client: HttpClient,
    min_live_ttl: int,
    local_file: Path | None = None,
) -> list[Channel]:
    raw = _load_raw(client, local_file)

    channels: list[Channel] = []
    for item in raw:
        if not _is_usable(item, min_live_ttl):
            continue

        channels.append(
            Channel(
                id=item["id"],
                name=item["name"],
                stream_url=item.get("stream_url") or item["url"],
                logo=item.get("logo", ""),
                group=localized_category_name(item.get("group", "web")),
                source="live_stream_catalog",
                tvg_id=item.get("tvg_id"),
                source_type=item.get("source_type"),
                source_url=item.get("source_url"),
                feed_id=item.get("feed_id"),
                status=item.get("status"),
                resolved_at=item.get("resolved_at"),
                expires_at=item.get("expires_at"),
                ttl_seconds=_current_ttl_seconds(item),
            )
        )

    return channels
