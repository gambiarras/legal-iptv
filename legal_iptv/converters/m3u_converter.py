from models.channel import Channel
from models.categories import get_categories_ordered

def __group_by_categories(channels):
    grouped = {}

    for channel in channels:
        if channel.group not in grouped:
            grouped[channel.group] = []

        grouped[channel.group].append(channel)
    
    return grouped

def __get_headers():
    tvg_urls = ",".join([
        'https://iptv-org.github.io/epg/guides/pt/mi.tv.xml.gz',
        'https://i.mjh.nz/Plex/all.xml',
        'https://raw.githubusercontent.com/matthuisman/i.mjh.nz/master/SamsungTVPlus/all.xml'
    ])

    return f'#EXTM3U refresh="3600" x-tvg-url="{tvg_urls}" tvg-url="{tvg_urls}"'

def __get_inf(channel):
    inf = ''

    inf = inf + f'#EXTINF:-1 group-title="{channel.group}" tvg-id="{channel.id}" tvg-name="{channel.name}" tvg-logo="{channel.logo}", {channel.name}\n'
    inf = inf + f'#EXTGRP:{channel.group}\n'
    inf = inf + f'{channel.url}'

    return inf

def convert(channels):
    m3u = ''
    m3u = m3u + __get_headers() + '\n\n\n'

    categories = get_categories_ordered()
    grouped_channels = __group_by_categories(channels)
    for category in categories:
        channels_by_group = grouped_channels.get(category)

        if channels_by_group is None:
            continue

        m3u = m3u + f'### Canais {category}' + '\n\n'

        for channel in channels_by_group:
            m3u = m3u + __get_inf(channel) + '\n\n'

    return m3u
