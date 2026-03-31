import json
from pathlib import Path

from legal_iptv.clients import HttpClient
from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name


LIVE_STREAM_CATALOG_URL = "https://raw.githubusercontent.com/gambiarras/youtube-live-channels/main/channels.json"


def _is_usable(item: dict, min_live_ttl: int) -> bool:
    if item.get("status") != "resolved":
        return False

    stream_url = item.get("stream_url") or item.get("url")
    if not stream_url:
        return False

    ttl_seconds = item.get("ttl_seconds")
    if ttl_seconds is not None and ttl_seconds < min_live_ttl:
        return False

    return True


def _load_raw(client: HttpClient, local_file: Path | None) -> list[dict]:
    if local_file is not None and local_file.exists():
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
                source_type=item.get("source_type"),
                source_url=item.get("source_url"),
                status=item.get("status"),
                resolved_at=item.get("resolved_at"),
                expires_at=item.get("expires_at"),
                ttl_seconds=item.get("ttl_seconds"),
            )
        )

    return channels