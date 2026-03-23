from collections import defaultdict

from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import CATEGORY_ORDER


EPG_URLS = [
    "https://iptv-epg.org/files/epg-br.xml",
    "https://i.mjh.nz/Plex/all.xml",
    "https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/SamsungTVPlus/all.xml",
]


def _render_header() -> str:
    tvg_urls = ",".join(EPG_URLS)
    return f'#EXTM3U refresh="3600" x-tvg-url="{tvg_urls}" tvg-url="{tvg_urls}"'


def _render_channel(channel: Channel) -> str:
    return (
        f'#EXTINF:-1 group-title="{channel.group}" tvg-id="{channel.id}" '
        f'tvg-name="{channel.name}" tvg-logo="{channel.logo}", {channel.name}\n'
        f'#EXTGRP:{channel.group}\n'
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