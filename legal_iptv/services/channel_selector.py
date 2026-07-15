import hashlib
import re
import unicodedata
from dataclasses import replace
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from legal_iptv.models import Channel


SOURCE_PRIORITY = {
    "live_stream_catalog": 300,
    "extra": 200,
    "iptv_org": 100,
}

CHUNKLIST_PATTERN = re.compile(r"chunklist(?:_w\d+)?\.m3u8")


def _score(channel: Channel) -> tuple:
    ttl = channel.ttl_seconds

    if ttl is None:
        ttl_state = 1
        ttl_value = 0
    elif ttl > 0:
        ttl_state = 2
        ttl_value = ttl
    else:
        ttl_state = 0
        ttl_value = 0

    return (
        _stream_url_quality(channel.stream_url),
        SOURCE_PRIORITY.get(channel.source, 0),
        1 if channel.status == "resolved" else 0,
        1 if channel.stream_url else 0,
        ttl_state,
        ttl_value,
        1 if channel.logo else 0,
    )


@lru_cache(maxsize=4096)
def _normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    simplified = re.sub(r"[^a-z0-9]+", " ", ascii_name.casefold())
    return " ".join(simplified.split())


@lru_cache(maxsize=8192)
def _normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    path = _normalize_hls_path(parsed.path.rstrip("/") or "/")
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            "",
            "",
        )
    )


@lru_cache(maxsize=8192)
def _normalize_hls_path(path: str) -> str:
    if not path.casefold().endswith(".m3u8"):
        return path

    path_parts = path.rsplit("/", 1)
    directory = path_parts[0] if len(path_parts) == 2 else ""
    filename = path_parts[-1].casefold()

    if CHUNKLIST_PATTERN.fullmatch(filename):
        return f"{directory}/playlist.m3u8" if directory else "playlist.m3u8"

    return path


@lru_cache(maxsize=8192)
def _stream_url_quality(url: str) -> tuple[int, int]:
    parsed = urlsplit(url.strip())
    filename = parsed.path.rsplit("/", 1)[-1].casefold()

    stable_playlist = 1 if filename in {"master.m3u8", "playlist.m3u8"} else 0
    has_query = 1 if parsed.query else 0

    return (stable_playlist, has_query)


def _unique_id(channel: Channel, used_ids: set[str]) -> str:
    if channel.id not in used_ids:
        return channel.id

    digest = hashlib.sha1(channel.stream_url.encode("utf-8")).hexdigest()[:8]
    suffix_parts = [channel.source]
    if channel.source_type:
        suffix_parts.append(channel.source_type)
    suffix_parts.append(digest)
    suffix = ".".join(part for part in suffix_parts if part)
    candidate_id = f"{channel.id}.{suffix}"
    counter = 2

    while candidate_id in used_ids:
        candidate_id = f"{channel.id}.{suffix}.{counter}"
        counter += 1

    return candidate_id


def _with_unique_ids(channels: list[Channel]) -> list[Channel]:
    used_ids: set[str] = set()
    unique_channels: list[Channel] = []

    for channel in channels:
        channel_id = _unique_id(channel, used_ids)
        used_ids.add(channel_id)
        unique_channels.append(
            channel if channel_id == channel.id else replace(channel, id=channel_id)
        )

    return unique_channels


def _alternative_key(channel: Channel) -> tuple[str, str] | None:
    name = _normalize_name(channel.name)
    if not name:
        return None

    return (channel.tvg_id or "", name)


def _with_alternative_names(channels: list[Channel]) -> list[Channel]:
    seen_counts: dict[tuple[str, str], int] = {}
    renamed_channels: list[Channel] = []

    for channel in channels:
        key = _alternative_key(channel)
        if key is None:
            renamed_channels.append(channel)
            continue

        count = seen_counts.get(key, 0)
        seen_counts[key] = count + 1
        if count == 0:
            renamed_channels.append(channel)
            continue

        renamed_channels.append(
            replace(channel, name=f"{channel.name} Alternativo {count}")
        )

    return renamed_channels


def select_best_channels(channels: list[Channel]) -> list[Channel]:
    selected: list[Channel] = []
    selected_urls: set[str] = set()

    ranked_channels = sorted(
        (channel for channel in channels if channel.stream_url),
        key=_score,
        reverse=True,
    )

    for channel in ranked_channels:
        url_key = _normalize_url(channel.stream_url)
        if url_key in selected_urls:
            continue

        selected.append(channel)
        selected_urls.add(url_key)

    return _with_unique_ids(_with_alternative_names(selected))
