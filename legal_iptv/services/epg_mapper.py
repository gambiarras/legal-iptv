import gzip
import json
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import resources
from io import BytesIO
from pathlib import Path

from legal_iptv.clients import HttpClient
from legal_iptv.io import write_json_atomic
from legal_iptv.models import Channel
from legal_iptv.services.epg_sources import EPG_INDEX_URLS


ALIASES_RESOURCE_NAME = "epg_aliases.json"
GENERIC_TVG_ID_PATTERN = re.compile(r"^(script[_-]?catalog|youtube|live)[._-]", re.IGNORECASE)
DISPLAY_NAME_PREFIX_PATTERN = re.compile(r"^[A-Z]{2}\s+-\s+")

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class EPGMapping:
    tvg_id: str
    display_name: str | None = None


def enrich_epg_metadata(
    channels: list[Channel],
    xmltv_aliases: dict[str, EPGMapping] | None = None,
) -> list[Channel]:
    aliases = _load_aliases()
    xmltv_aliases = xmltv_aliases or {}
    discovered_aliases = _discover_aliases(channels)

    enriched: list[Channel] = []
    for channel in channels:
        mapping = _find_mapping(channel, aliases, xmltv_aliases, discovered_aliases)
        tvg_id = _resolved_tvg_id(channel, mapping)
        name = mapping.display_name if mapping and mapping.display_name else channel.name
        enriched.append(replace(channel, name=name, tvg_id=tvg_id))

    return enriched


def _find_mapping(
    channel: Channel,
    aliases: dict[str, EPGMapping],
    xmltv_aliases: dict[str, EPGMapping],
    discovered_aliases: dict[str, EPGMapping],
) -> EPGMapping | None:
    candidates = [
        channel.tvg_id or "",
        channel.name,
    ]

    for value in candidates:
        key = _normalize(value)
        if key in xmltv_aliases:
            return xmltv_aliases[key]

    for value in candidates:
        key = _normalize(value)
        if key in aliases:
            return aliases[key]

    for value in candidates:
        key = _normalize(value)
        if key in discovered_aliases:
            return discovered_aliases[key]

    return None


def load_xmltv_aliases(
    client: HttpClient,
    *,
    cache_file: Path | None = None,
    cache_ttl_seconds: int = 43200,
    force_refresh: bool = False,
) -> dict[str, EPGMapping]:
    if cache_file is not None and not force_refresh:
        cached_aliases = _load_fresh_xmltv_aliases_cache(
            cache_file,
            max_age_seconds=cache_ttl_seconds,
        )
        if cached_aliases is not None:
            logger.info(
                "Loaded EPG aliases from cache aliases=%s cache_file=%s",
                len(cached_aliases),
                cache_file,
            )
            return cached_aliases

    aliases = _fetch_xmltv_aliases(client)
    if aliases:
        if cache_file is not None:
            _write_xmltv_aliases_cache(cache_file, aliases)
        return aliases

    if cache_file is not None:
        stale_aliases = _load_xmltv_aliases_cache(cache_file, max_age_seconds=None)
        if stale_aliases is not None:
            logger.warning(
                "Using stale EPG aliases cache aliases=%s cache_file=%s",
                len(stale_aliases),
                cache_file,
            )
            return stale_aliases

    return aliases


def _fetch_xmltv_aliases(client: HttpClient) -> dict[str, EPGMapping]:
    aliases: dict[str, EPGMapping] = {}

    for url in EPG_INDEX_URLS:
        try:
            payload = client.get_bytes(url)
            source_aliases = parse_xmltv_aliases(
                payload,
                compressed=url.endswith(".gz"),
                require_programmes=True,
            )
            for key, mapping in source_aliases.items():
                aliases.setdefault(key, mapping)
        except Exception as exc:
            logger.warning("Failed to load EPG index url=%s error=%s", url, exc)

    logger.info("Loaded EPG aliases from sources aliases=%s", len(aliases))
    return aliases


def parse_xmltv_aliases(
    payload: bytes,
    *,
    compressed: bool = False,
    require_programmes: bool = False,
) -> dict[str, EPGMapping]:
    data = gzip.decompress(payload) if compressed else payload
    channel_candidates: dict[str, dict[str, set[EPGMapping]]] = {}
    programmed_channel_ids: set[str] = set()
    candidates: dict[str, set[EPGMapping]] = defaultdict(set)

    for _, element in ET.iterparse(BytesIO(data), events=("end",)):
        if element.tag == "programme":
            channel_id = element.attrib.get("channel")
            if channel_id:
                programmed_channel_ids.add(channel_id)
            element.clear()
            continue

        if element.tag != "channel":
            continue

        tvg_id = element.attrib.get("id")
        if not tvg_id:
            element.clear()
            continue

        mapping = EPGMapping(tvg_id=tvg_id)
        item_candidates: dict[str, set[EPGMapping]] = defaultdict(set)
        _add_candidate(item_candidates, tvg_id, mapping)

        for display_name in element.findall("display-name"):
            text = display_name.text or ""
            _add_candidate(item_candidates, text, mapping)
            _add_candidate(item_candidates, _strip_display_prefix(text), mapping)

        channel_candidates[tvg_id] = item_candidates
        element.clear()

    for tvg_id, item_candidates in channel_candidates.items():
        if require_programmes and tvg_id not in programmed_channel_ids:
            continue

        for key, mappings in item_candidates.items():
            candidates[key].update(mappings)

    return _unique_mappings(candidates)


def _add_candidate(
    candidates: dict[str, set[EPGMapping]],
    value: str | None,
    mapping: EPGMapping,
) -> None:
    key = _normalize(value)
    if key:
        candidates[key].add(mapping)


def _unique_mappings(candidates: dict[str, set[EPGMapping]]) -> dict[str, EPGMapping]:
    return {
        key: next(iter(values))
        for key, values in candidates.items()
        if len(values) == 1
    }


def _load_fresh_xmltv_aliases_cache(
    cache_file: Path,
    *,
    max_age_seconds: int,
) -> dict[str, EPGMapping] | None:
    return _load_xmltv_aliases_cache(cache_file, max_age_seconds=max_age_seconds)


def _load_xmltv_aliases_cache(
    cache_file: Path,
    *,
    max_age_seconds: int | None,
) -> dict[str, EPGMapping] | None:
    if not cache_file.exists():
        return None

    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read EPG aliases cache cache_file=%s", cache_file)
        return None

    generated_at = _parse_datetime(payload.get("generated_at"))
    if generated_at is None:
        return None

    if max_age_seconds is not None:
        cutoff = _utc_now() - timedelta(seconds=max_age_seconds)
        if generated_at < cutoff:
            return None

    raw_aliases = payload.get("aliases")
    if not isinstance(raw_aliases, dict):
        return None

    aliases: dict[str, EPGMapping] = {}
    for key, item in raw_aliases.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            continue

        tvg_id = item.get("tvg_id")
        if not isinstance(tvg_id, str) or not tvg_id:
            continue

        display_name = item.get("display_name")
        aliases[key] = EPGMapping(
            tvg_id=tvg_id,
            display_name=display_name if isinstance(display_name, str) else None,
        )

    return aliases


def _write_xmltv_aliases_cache(
    cache_file: Path,
    aliases: dict[str, EPGMapping],
) -> None:
    payload = {
        "generated_at": _utc_now().isoformat(),
        "aliases": {
            key: {
                "tvg_id": mapping.tvg_id,
                "display_name": mapping.display_name,
            }
            for key, mapping in sorted(aliases.items())
        },
    }
    write_json_atomic(cache_file, payload)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _strip_display_prefix(value: str) -> str:
    return DISPLAY_NAME_PREFIX_PATTERN.sub("", value).strip()


def _resolved_tvg_id(channel: Channel, mapping: EPGMapping | None) -> str | None:
    if mapping:
        return mapping.tvg_id

    if _is_reliable_tvg_id(channel.tvg_id):
        return channel.tvg_id

    return None


def _discover_aliases(channels: list[Channel]) -> dict[str, EPGMapping]:
    candidates: dict[str, set[EPGMapping]] = {}

    for channel in channels:
        if not _is_reliable_tvg_id(channel.tvg_id):
            continue

        mapping = EPGMapping(tvg_id=channel.tvg_id, display_name=channel.name)
        for value in (channel.name, channel.tvg_id):
            key = _normalize(value)
            if not key:
                continue
            candidates.setdefault(key, set()).add(mapping)

    return {
        key: next(iter(values))
        for key, values in candidates.items()
        if len(values) == 1
    }


@lru_cache(maxsize=1)
def _load_aliases() -> dict[str, EPGMapping]:
    resource = resources.files("legal_iptv.resources").joinpath(ALIASES_RESOURCE_NAME)
    if not resource.is_file():
        return {}

    raw = json.loads(resource.read_text(encoding="utf-8"))
    aliases: dict[str, EPGMapping] = {}

    for item in raw:
        mapping = EPGMapping(
            tvg_id=item["tvg_id"],
            display_name=item.get("display_name"),
        )
        for alias in item.get("aliases", []):
            aliases[_normalize(alias)] = mapping

    return aliases


def _is_reliable_tvg_id(value: str | None) -> bool:
    if not value:
        return False

    if GENERIC_TVG_ID_PATTERN.search(value):
        return False

    return "." in value


@lru_cache(maxsize=8192)
def _normalize(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    simplified = re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold())
    return " ".join(simplified.split())
