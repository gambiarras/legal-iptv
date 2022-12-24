from urllib.request import urlopen

import json

from models.channel import Channel
from models.categories import localized_category_name

def __request(url):
    return json.load(urlopen(url))

def __channels(channels_data):
    channels = []

    for channel_data in channels_data:
        channels.append(Channel(
            channel_data['id'],
            channel_data['name'],
            channel_data['url'],
            channel_data['logo'],
            localized_category_name(channel_data['group'])
        ))

    return [channel for channel in channels if channel is not None]

def fetch_channels():
    url = 'https://raw.githubusercontent.com/gambiarras/youtube-live-channels/main/channels.json'
    return __channels(__request(url))
