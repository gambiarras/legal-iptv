"""Shared feed identity and eligibility for every published playlist."""

import re
from dataclasses import replace
from urllib.parse import quote

from legal_iptv.local_config import load_resource, runtime_value
from legal_iptv.models import Channel
from legal_iptv.services.companion_reference import identity_key

# Match option labels, not broadcaster names such as TV Alternativa.
ALTERNATIVE = re.compile(
    r"(?:\s|^)(?:alternativo(?:\s*\d+)?|alternative(?:\s*\d+)?|backup(?:\s*\d+)?|reserva(?:\s*\d+)?)\s*$",
    re.IGNORECASE,
)
ADULT = re.compile(r"(?:\b(?:xxx|porn|adultos?|adults|nsfw)\b|\+18|18\+)", re.I)
QUALITY_SUFFIX = re.compile(r"(?:\s*\[(?:4K|FHD|HD|SD|HEVC)\])+\s*$", re.I)


def load_policy() -> dict:
    return load_resource("companion_policy.json")


def canonical_key(name: str, policy: dict | None = None) -> str:
    policy = policy or load_policy()
    key = identity_key(name)
    return policy["aliases"].get(key, key)


def paid_name(name: str, policy: dict | None = None) -> bool:
    key = identity_key(name)
    return any(key == prefix or (
        (len(prefix) >= 4 or prefix in (policy or load_policy()).get("short_paid_prefixes", []))
        and key.startswith(prefix)
    ) for prefix in (policy or load_policy())["paid_name_prefixes"])


def rejection_reason(channel: Channel, policy: dict | None = None) -> str | None:
    policy = policy or load_policy()
    provenance = " ".join(str(value or "") for value in (
        channel.provider_id, channel.source_type, channel.id,
        channel.source_url, channel.stream_url,
    )).casefold()
    if any(part in provenance for part in policy["excluded_provider_fragments"]):
        return "excluded_provider"
    text = " ".join((channel.name, channel.raw_group or "", channel.group))
    if (channel.is_adult or ADULT.search(text)
            or identity_key(channel.raw_group or channel.group) == "adult"):
        return "adult"
    if (ALTERNATIVE.search(QUALITY_SUFFIX.sub("", channel.name).strip().rstrip(")]"))
            or identity_key(channel.raw_group or channel.group) in {"alternativos", "alternatives"}):
        return "alternative"
    if (channel.provider_id in policy["rest_catalog_1_provider_ids"]
            or channel.source_type == "script_discovered") and paid_name(channel.name, policy):
        return "paid_channel"
    return None


def canonicalize_channels(channels: list[Channel]) -> list[Channel]:
    """Share IDs across providers without erasing affiliate/city names.

    An upstream guide ID shared by different named feeds is ambiguous. Give
    those feeds distinct IDs instead of assigning another region's schedule.
    """
    feed_aliases = runtime_value("feed_id_aliases", {})
    feeds = {id(channel): feed_aliases.get(str(channel.feed_id), channel.feed_id) if channel.feed_id else None
             for channel in channels}
    names = {id(channel): (channel.name.split(" - ", 1)[0].strip()
                          if channel.source_type == "video_platform" or channel.source == "video_platform"
                          else channel.name) for channel in channels}
    bases = {id(channel): canonical_key(names[id(channel)]) for channel in channels}
    keys = {id(channel): ("feed:" + quote(str(feeds[id(channel)]).strip(), safe="._-"))
            if feeds[id(channel)] else bases[id(channel)] for channel in channels}
    ids_by_key: dict[str, list[str]] = {}
    keys_by_id: dict[str, set[str]] = {}
    for channel in channels:
        key = keys[id(channel)]
        if channel.tvg_id:
            ids_by_key.setdefault(key, []).append(channel.tvg_id)
            keys_by_id.setdefault(channel.tvg_id, set()).add(key)
    result = []
    known_ids = runtime_value("known_ids", {})
    display_names = runtime_value("display_names", {})
    for channel in channels:
        key = keys[id(channel)]
        ambiguous = channel.epg_id_ambiguous or bool(channel.tvg_id and len(keys_by_id[channel.tvg_id]) > 1)
        candidates = ids_by_key.get(key, ())
        explicit = known_ids.get(key)
        tvg_id = explicit or (None if ambiguous else next(
            (value for value in candidates if len(keys_by_id[value]) == 1), None))
        if tvg_id is None:
            tvg_id = "Local." + key.replace(":", ".") + ".br"
        name = display_names.get(key, display_names.get(bases[id(channel)], names[id(channel)]))
        result.append(replace(channel, name=name, tvg_id=tvg_id, feed_id=feeds[id(channel)],
                              epg_id_ambiguous=ambiguous and explicit is None,
                              logical_channel_id="channel:" + key))
    return result
