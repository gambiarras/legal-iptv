import gzip
import json
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, replace
from io import BytesIO
from importlib import resources

from legal_iptv.clients import HttpClient
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
        if key in aliases:
            return aliases[key]

    for value in candidates:
        key = _normalize(value)
        if key in discovered_aliases:
            return discovered_aliases[key]

    for value in candidates:
        key = _normalize(value)
        if key in xmltv_aliases:
            return xmltv_aliases[key]

    return None


def load_xmltv_aliases(client: HttpClient) -> dict[str, EPGMapping]:
    aliases: dict[str, set[EPGMapping]] = defaultdict(set)

    for url in EPG_INDEX_URLS:
        try:
            payload = client.get_bytes(url)
            for key, mapping in parse_xmltv_aliases(payload, compressed=url.endswith(".gz")).items():
                aliases[key].add(mapping)
        except Exception as exc:
            logger.warning("Failed to load EPG index url=%s error=%s", url, exc)

    return _unique_mappings(aliases)


def parse_xmltv_aliases(payload: bytes, *, compressed: bool = False) -> dict[str, EPGMapping]:
    data = gzip.decompress(payload) if compressed else payload
    candidates: dict[str, set[EPGMapping]] = defaultdict(set)

    for _, element in ET.iterparse(BytesIO(data), events=("end",)):
        if element.tag != "channel":
            continue

        tvg_id = element.attrib.get("id")
        if not tvg_id:
            element.clear()
            continue

        mapping = EPGMapping(tvg_id=tvg_id)
        _add_candidate(candidates, tvg_id, mapping)

        for display_name in element.findall("display-name"):
            text = display_name.text or ""
            _add_candidate(candidates, text, mapping)
            _add_candidate(candidates, _strip_display_prefix(text), mapping)

        element.clear()

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


def _normalize(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    simplified = re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold())
    return " ".join(simplified.split())
