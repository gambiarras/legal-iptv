import hashlib
import re
import unicodedata
from dataclasses import replace
from functools import lru_cache

from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import canonical_category_name, category_key
from legal_iptv.services.channel_selector import select_best_channels
from legal_iptv.services.profile_config import PlaylistConfiguration, PlaylistProfile
from legal_iptv.services.publication import is_exportable
from legal_iptv.services.video_platform_transport import is_public_video_platform_hls
from legal_iptv.services.channel_identity import canonicalize_channels, rejection_reason


VARIANT_SUFFIX_PATTERN = re.compile(
    r"\s+(?:\[(?:4K|FHD|HD|SD|HEVC)\])(?:\s+\[(?:4K|FHD|HD|SD|HEVC)\])*$",
    re.IGNORECASE,
)
EXPLICIT_ADULT_PATTERN = re.compile(
    r"(?:^|[\s(/_-])(?:\+18|18\+|adultos?|adults?|nsfw|xxx)(?:$|[\s)/_-])",
    re.IGNORECASE,
)
EXPLICIT_ADULT_NAME_PATTERN = re.compile(
    r"(?:^|[\s(/_-])(?:\+18|18\+|nsfw|xxx)(?:$|[\s)/_-])",
    re.IGNORECASE,
)
QUALITY_PATTERN = re.compile(
    r"(?<![a-z0-9])(2160p|4k|uhd|1080p|full\s*hd|fhd|720p|hd|576p|540p|480p|360p|sd)(?![a-z0-9])",
    re.IGNORECASE,
)
HEVC_PATTERN = re.compile(r"(?<![a-z0-9])(hevc|h\.?265)(?![a-z0-9])", re.IGNORECASE)
ALTERNATIVE_NAME_PATTERN = re.compile(r"\s+alternativo(?:\s+\d+)?$", re.IGNORECASE)


@lru_cache(maxsize=8192)
def _normalize(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).split())


def _provider_key(channel: Channel) -> str:
    return channel.provider_id or channel.source_type or channel.source


def _logical_key(channel: Channel) -> str:
    if channel.logical_channel_id and channel.logical_channel_id.startswith("channel:"):
        return channel.logical_channel_id
    if channel.tvg_id and "." in channel.tvg_id:
        return f"tvg:{_normalize(channel.tvg_id)}"
    if channel.logical_channel_id:
        return f"logical:{_normalize(channel.logical_channel_id)}"
    base_name = VARIANT_SUFFIX_PATTERN.sub("", channel.name).strip()
    return f"name:{_normalize(base_name)}"


def _normalized_url(url: str) -> str:
    return url.strip()


def _priority(channel: Channel, configuration: PlaylistConfiguration) -> int:
    provider = _provider_key(channel)
    if provider in configuration.provider_priorities:
        return configuration.provider_priorities[provider]
    return configuration.provider_priorities.get(channel.source, 0)


def _is_explicit_adult(channel: Channel) -> bool:
    if channel.is_adult:
        return True
    return bool(
        EXPLICIT_ADULT_PATTERN.search(channel.raw_group or channel.group)
        or EXPLICIT_ADULT_NAME_PATTERN.search(channel.name)
    )


def _allowed_by_profile(channel: Channel, profile: PlaylistProfile) -> bool:
    if profile.curated_sources and rejection_reason(channel):
        return False
    if channel.removed or channel.status == "removed" or not channel.stream_url:
        return False
    if profile.include_sources and channel.source not in profile.include_sources:
        return False
    if channel.id in profile.exclude_ids:
        return False
    if profile.include_ids and channel.id not in profile.include_ids:
        return False
    if channel.group in profile.exclude_groups or (channel.raw_group or "") in profile.exclude_groups:
        return False
    if profile.include_groups and not {
        channel.group,
        channel.raw_group or "",
    }.intersection(profile.include_groups):
        return False
    if channel.variant_id in profile.exclude_variants:
        return False
    if profile.include_variants and channel.variant_id not in profile.include_variants:
        return False
    if profile.exclude_explicit_adult and _is_explicit_adult(channel):
        return False
    if profile.require_publishable_static:
        if (
            ((not channel.publishable_static or channel.requires_dynamic_resolution)
             and not is_public_video_platform_hls(channel))
            or channel.secret_refs
            or channel.delivery_mode in {"dai", "ssai"}
        ):
            return False
    return True


def _apply_overrides(
    channel: Channel,
    configuration: PlaylistConfiguration,
) -> Channel:
    values = configuration.overrides.get(channel.id)
    if values is None and channel.logical_channel_id:
        values = configuration.overrides.get(channel.logical_channel_id)
    if not values:
        return channel

    allowed = {"name", "logo", "group", "tvg_id", "logical_channel_id"}
    return replace(channel, **{key: value for key, value in values.items() if key in allowed})


def _map_group(channel: Channel, configuration: PlaylistConfiguration) -> Channel:
    raw_group = channel.raw_group or channel.group
    mapped = configuration.category_map.get(category_key(raw_group))
    if mapped is None:
        mapped = raw_group if channel.group == "Variedades" else channel.group
    mapped = canonical_category_name(mapped)
    return channel if mapped == channel.group else replace(channel, group=mapped)


def _normalize_variant_name(channel: Channel) -> Channel:
    values = " ".join(
        str(value)
        for value in (
            channel.variant_label,
            channel.resolution,
            channel.codec,
        )
        if value
    )
    quality = None
    match = QUALITY_PATTERN.search(values)
    if match:
        token = re.sub(r"\s+", "", match.group(1).casefold())
        if token in {"2160p", "4k", "uhd"}:
            quality = "4K"
        elif token in {"1080p", "fullhd", "fhd"}:
            quality = "FHD"
        elif token in {"720p", "hd"}:
            quality = "HD"
        else:
            quality = "SD"
    hevc = bool(HEVC_PATTERN.search(values))
    labels = [label for label in (quality, "HEVC" if hevc else None) if label]
    if not labels:
        return channel
    base_name = VARIANT_SUFFIX_PATTERN.sub("", channel.name).strip()
    name = base_name + "".join(f" [{label}]" for label in labels)
    return channel if name == channel.name else replace(channel, name=name)


def _base_candidates(
    channels: list[Channel],
    profile: PlaylistProfile,
) -> list[Channel]:
    return [
        channel
        for channel in channels
        if _allowed_by_profile(channel, profile)
        and (
            channel.source != "live_stream_catalog"
            or not profile.include_live_source_types
            or channel.source_type is None
            or channel.source_type in profile.include_live_source_types
        )
    ]


def _unique_ids(channels: list[Channel]) -> list[Channel]:
    used: set[str] = set()
    result: list[Channel] = []
    for channel in channels:
        channel_id = channel.id
        if channel_id in used:
            digest = hashlib.sha1(channel.stream_url.encode("utf-8")).hexdigest()[:8]
            channel_id = f"{channel.id}.{_provider_key(channel)}.{digest}"
        used.add(channel_id)
        result.append(channel if channel_id == channel.id else replace(channel, id=channel_id))
    return result


def _select_logical_providers(
    channels: list[Channel],
    configuration: PlaylistConfiguration,
) -> list[Channel]:
    # A single pass chooses source before quality. Headers remain attached to
    # the winning transport; URL equality alone must not merge different feeds.
    winners: dict[str, tuple[tuple, Channel]] = {}
    for channel in channels:
        key = _logical_key(channel)
        score = (_priority(channel, configuration), *_variant_quality_score(channel, configuration))
        current = winners.get(key)
        if current is None or score > current[0]:
            winners[key] = (score, channel)
    return _unique_ids([item[1] for item in winners.values()])


def _variant_logical_key(channel: Channel) -> str:
    if channel.logical_channel_id and channel.logical_channel_id.startswith("channel:"):
        return channel.logical_channel_id
    base_name = VARIANT_SUFFIX_PATTERN.sub("", channel.name).strip()
    if channel.tvg_id and base_name:
        return (
            f"provider:{_normalize(_provider_key(channel))}:"
            f"tvg:{_normalize(channel.tvg_id)}:name:{_normalize(base_name)}"
        )
    if channel.logical_channel_id:
        return f"logical:{_normalize(channel.logical_channel_id)}"
    return _logical_key(channel)


def _variant_quality_score(
    channel: Channel,
    configuration: PlaylistConfiguration,
) -> tuple[int, int, int]:
    values = " ".join(
        str(value)
        for value in (
            channel.name,
            channel.variant_label,
            channel.resolution,
        )
        if value
    )
    match = QUALITY_PATTERN.search(values)
    quality = ""
    if match:
        token = re.sub(r"\s+", "", match.group(1).casefold())
        if token in {"2160p", "4k", "uhd"}:
            quality = "4K"
        elif token in {"1080p", "fullhd", "fhd"}:
            quality = "FHD"
        elif token in {"720p", "hd"}:
            quality = "HD"
        else:
            quality = "SD"
    ranks = {
        label: len(configuration.variant_quality_order) - index
        for index, label in enumerate(configuration.variant_quality_order)
    }
    return (
        ranks.get(quality, 0),
        channel.bitrate or 0,
        1 if channel.status == "resolved" else 0,
    )


def _select_best_variants(
    channels: list[Channel],
    configuration: PlaylistConfiguration,
) -> list[Channel]:
    logical_groups: dict[str, list[Channel]] = {}
    for channel in channels:
        logical_groups.setdefault(_variant_logical_key(channel), []).append(channel)

    result: list[Channel] = []
    for variants in logical_groups.values():
        _, winner = max(
            enumerate(variants),
            key=lambda item: (_variant_quality_score(item[1], configuration), -item[0]),
        )
        if (
            ALTERNATIVE_NAME_PATTERN.search(winner.name)
            and winner.group != configuration.alternatives_group
        ):
            winner = replace(winner, group=configuration.alternatives_group)
        result.append(winner)
    return result


def select_profile_channels(
    channels: list[Channel],
    configuration: PlaylistConfiguration,
    profile_name: str,
    *,
    player_profile: str = "portable",
) -> list[Channel]:
    profile = configuration.profile(profile_name)
    if profile.curated_sources:
        channels = canonicalize_channels([channel for channel in channels
                                          if not rejection_reason(channel)])
    if profile.selection_mode == "legacy":
        return select_best_channels(_base_candidates(channels, profile))

    inherited_channels: list[Channel] = []
    if profile.inherits:
        inherited_channels = select_profile_channels(
            channels,
            configuration,
            profile.inherits,
            player_profile=player_profile,
        )
    combined = inherited_channels + channels
    seen_candidates: set[tuple] = set()
    candidates: list[Channel] = []
    for channel in combined:
        if not _allowed_by_profile(channel, profile):
            continue
        candidate_key = (channel.id, channel.stream_url, channel.source, _provider_key(channel),
                         tuple(sorted((key.casefold(), value)
                                      for key, value in channel.request_headers.items())),
                         channel.resolution, channel.codec, channel.bitrate, channel.variant_label)
        if candidate_key in seen_candidates:
            continue
        seen_candidates.add(candidate_key)
        candidates.append(
            _normalize_variant_name(
                _map_group(_apply_overrides(channel, configuration), configuration)
            )
        )
    candidates = [
        channel
        for channel in candidates
        if is_exportable(channel, player_profile)
    ]
    selected = _select_logical_providers(candidates, configuration)
    return _select_best_variants(selected, configuration)
