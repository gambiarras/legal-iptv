from urllib.request import urlopen

import json

from models.channel import Channel
from models.categories import localized_category_name

def __request(url):
    return json.load(urlopen(url))

def __filter_channels(channels):
    return [
        channel for channel in channels
            if channel['country'] == 'BR' and channel['is_nsfw'] is False
    ]

def __filter_streams(streams, channels):
    return [
        stream for stream in streams
            if 'status' in stream 
                and stream['status'] != 'error' and stream['status'] != 'blocked'
                and [channel for channel in channels if channel['id'] == stream['channel']]
    ]

def __get_element(json_list, key, value):
    return next(filter(lambda element: key in element and element[key] == value, json_list), None)

def __channels(channels_data, streams_data):
    channels = []

    for channel_data in channels_data:
        stream_element = __get_element(streams_data, "channel", channel_data["id"])

        if stream_element is not None:
            category = next(iter(channel_data['categories']), None)
            channels.append(Channel(
                channel_data['id'],
                channel_data['name'],
                stream_element['url'],
                channel_data['logo'],
                localized_category_name(category)
            ))
        else:
            channels.append(None)

    return [channel for channel in channels if channel is not None]

def fetch_channels():
    BASE_URL = 'https://iptv-org.github.io/api'

    streams_url = f'{BASE_URL}/streams.json'
    channels_url = f'{BASE_URL}/channels.json'

    channels = __filter_channels(__request(channels_url))
    streams = __filter_streams(__request(streams_url), channels)

    return __channels(channels, streams)
