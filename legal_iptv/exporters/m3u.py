from collections import defaultdict
from urllib.parse import urlencode

from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import CATEGORY_ORDER
from legal_iptv.services.epg_sources import EPG_URLS, PRIMARY_EPG_URL
from legal_iptv.services.publication import is_exportable


def _sanitize_attribute(value: str | None) -> str:
    if value is None:
        return ""

    return " ".join(value.replace('"', "'").split())


def _sanitize_display_name(value: str) -> str:
    return " ".join(value.split())


def _render_header(guide_url: str | None = None, *, legacy_guide_urls: bool = True) -> str:
    candidates = [PRIMARY_EPG_URL]
    if guide_url:
        candidates.extend(item.strip() for item in guide_url.split(","))
    elif legacy_guide_urls:
        candidates.extend(EPG_URLS)
    tvg_urls = ",".join(dict.fromkeys(item for item in candidates if item))
    if not tvg_urls:
        return '#EXTM3U refresh="3600"'
    return f'#EXTM3U refresh="3600" x-tvg-url="{tvg_urls}" tvg-url="{tvg_urls}"'


def _url_with_headers(channel: Channel) -> str:
    if not channel.request_headers:
        return channel.stream_url
    return f"{channel.stream_url}|{urlencode(channel.request_headers)}"


def _kodi_properties(channel: Channel) -> list[str]:
    properties: list[str] = []
    protocol = (channel.protocol or "").casefold()
    if protocol in {"hls", "dash"}:
        manifest_type = "mpd" if protocol == "dash" else "hls"
        properties.extend(
            [
                "#KODIPROP:inputstream=inputstream.adaptive",
                f"#KODIPROP:inputstream.adaptive.manifest_type={manifest_type}",
            ]
        )

    if channel.drm:
        drm_type = str(channel.drm.get("type") or channel.drm.get("key_system") or "").casefold()
        license_url = channel.drm.get("license_url")
        if drm_type in {"widevine", "com.widevine.alpha"} and license_url:
            license_headers = channel.drm.get("license_headers")
            encoded_headers = urlencode(license_headers) if isinstance(license_headers, dict) else ""
            properties.append(
                "#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha"
            )
            properties.append(
                f"#KODIPROP:inputstream.adaptive.license_key={license_url}|{encoded_headers}|R{{SSM}}|"
            )
    return properties


def _render_channel(channel: Channel, player_profile: str = "portable") -> str:
    group = _sanitize_attribute(channel.group)
    tvg_id = _sanitize_attribute(channel.tvg_id)
    name = _sanitize_attribute(channel.name)
    logo = _sanitize_attribute(channel.logo)
    display_name = _sanitize_display_name(channel.name)

    lines = [
        f'#EXTINF:-1 group-title="{group}" tvg-id="{tvg_id}" '
        f'tvg-name="{name}" tvg-logo="{logo}", {display_name}',
        f'#EXTGRP:{group}',
    ]
    if player_profile == "kodi":
        lines.extend(_kodi_properties(channel))
    lines.append(_url_with_headers(channel))
    return "\n".join(lines)


def render_m3u(
    channels: list[Channel],
    *,
    guide_url: str | None = None,
    player_profile: str = "portable",
    category_order: tuple[str, ...] | list[str] | None = None,
    legacy_guide_urls: bool = True,
    enforce_capabilities: bool = False,
    alternatives_group: str = "Alternativos",
) -> str:
    grouped: dict[str, list[Channel]] = defaultdict(list)

    for channel in channels:
        if enforce_capabilities and not is_exportable(channel, player_profile):
            continue
        grouped[channel.group].append(channel)

    lines = [_render_header(guide_url, legacy_guide_urls=legacy_guide_urls), "", ""]

    ordered_categories = list(category_order or CATEGORY_ORDER)
    if category_order is not None:
        remaining = sorted(
            set(grouped).difference(ordered_categories, {alternatives_group}),
            key=str.casefold,
        )
        alternative = [alternatives_group] if alternatives_group in grouped else []
        ordered_categories = [
            category for category in ordered_categories if category != alternatives_group
        ] + remaining + alternative

    for category in ordered_categories:
        items = grouped.get(category)
        if not items:
            continue

        lines.append(f"### Canais {category}")
        lines.append("")

        for channel in sorted(items, key=lambda item: item.name.casefold()):
            lines.append(_render_channel(channel, player_profile))
            lines.append("")

    return "\n".join(lines).strip() + "\n"
