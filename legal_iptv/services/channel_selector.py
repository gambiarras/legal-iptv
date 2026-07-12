import hashlib
import re
import unicodedata
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from legal_iptv.models import Channel


SOURCE_PRIORITY = {
    "live_stream_catalog": 300,
    "extra": 200,
    "iptv_org": 100,
}

SIMILAR_NAME_THRESHOLD = 0.92


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
        SOURCE_PRIORITY.get(channel.source, 0),
        1 if channel.status == "resolved" else 0,
        1 if channel.stream_url else 0,
        ttl_state,
        ttl_value,
        1 if channel.logo else 0,
    )


def _normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    simplified = re.sub(r"[^a-z0-9]+", " ", ascii_name.casefold())
    return " ".join(simplified.split())


def _normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            parsed.query,
            "",
        )
    )


def _has_similar_name(left: Channel, right: Channel) -> bool:
    left_name = _normalize_name(left.name)
    right_name = _normalize_name(right.name)

    if not left_name or not right_name:
        return False

    if left_name == right_name:
        return True

    return SequenceMatcher(None, left_name, right_name).ratio() >= SIMILAR_NAME_THRESHOLD


def _is_duplicate_candidate(candidate: Channel, existing: Channel) -> bool:
    if candidate.id == existing.id:
        return True

    return _has_similar_name(candidate, existing)


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


def select_best_channels(channels: list[Channel]) -> list[Channel]:
    selected: list[Channel] = []
    selected_by_url: dict[str, list[Channel]] = {}

    ranked_channels = sorted(
        (channel for channel in channels if channel.stream_url),
        key=_score,
        reverse=True,
    )

    for channel in ranked_channels:
        url_key = _normalize_url(channel.stream_url)
        existing_channels = selected_by_url.setdefault(url_key, [])

        if any(_is_duplicate_candidate(channel, existing) for existing in existing_channels):
            continue

        selected.append(channel)
        existing_channels.append(channel)

    return _with_unique_ids(selected)
