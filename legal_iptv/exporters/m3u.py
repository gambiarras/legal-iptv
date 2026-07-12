from collections import defaultdict

from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import CATEGORY_ORDER


EPG_URLS = [
    "https://iptv-epg.org/files/epg-br.xml",
    "https://i.mjh.nz/Plex/all.xml",
    "https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/SamsungTVPlus/all.xml",
]


def _sanitize_attribute(value: str | None) -> str:
    if value is None:
        return ""

    return " ".join(value.replace('"', "'").split())


def _sanitize_display_name(value: str) -> str:
    return " ".join(value.split())


def _render_header() -> str:
    tvg_urls = ",".join(EPG_URLS)
    return f'#EXTM3U refresh="3600" x-tvg-url="{tvg_urls}" tvg-url="{tvg_urls}"'


def _render_channel(channel: Channel) -> str:
    group = _sanitize_attribute(channel.group)
    channel_id = _sanitize_attribute(channel.id)
    name = _sanitize_attribute(channel.name)
    logo = _sanitize_attribute(channel.logo)
    display_name = _sanitize_display_name(channel.name)

    return (
        f'#EXTINF:-1 group-title="{group}" tvg-id="{channel_id}" '
        f'tvg-name="{name}" tvg-logo="{logo}", {display_name}\n'
        f'#EXTGRP:{group}\n'
        f'{channel.stream_url}'
    )


def render_m3u(channels: list[Channel]) -> str:
    grouped: dict[str, list[Channel]] = defaultdict(list)

    for channel in channels:
        grouped[channel.group].append(channel)

    lines = [_render_header(), "", ""]

    for category in CATEGORY_ORDER:
        items = grouped.get(category)
        if not items:
            continue

        lines.append(f"### Canais {category}")
        lines.append("")

        for channel in sorted(items, key=lambda item: item.name.casefold()):
            lines.append(_render_channel(channel))
            lines.append("")

    return "\n".join(lines).strip() + "\n"
