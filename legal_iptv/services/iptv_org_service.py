from urllib.request import urlopen
from functools import partial

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
            if [channel for channel in channels if channel['id'] == stream['channel']]
    ]

def __filter_logos(logos, channels):
    return [
        logo for logo in logos
            if [channel for channel in channels if channel['id'] == logo['channel']]
    ]

def __get_element(json_list, key, value):
    return next(__get_elements(json_list, key, value), None)

def __get_elements(json_list, key, value):
    return filter(lambda element: key in element and element[key] == value, json_list)

def __make_channel(stream, channel_data, category, logo):
    return Channel(
        channel_data['id'],
        channel_data['name'],
        stream['url'],
        logo,
        localized_category_name(category)
    )

def __channels(channels_data, streams_data, logos_data):
    channels = []

    for channel_data in channels_data:
        stream_elements = __get_elements(streams_data, "channel", channel_data["id"])

        if stream_elements:
            logo_element = __get_element(logos_data, "channel", channel_data["id"])
            logo_url = logo_element.get("url", None) if logo_element is not None else None

            category = next(iter(channel_data['categories']), 'general')
            partial_make_channel = partial(__make_channel, channel_data=channel_data, category=category, logo=logo_url)
            channels = channels + list(map(partial_make_channel, stream_elements))
        else:
            channels.append(None)

    return [channel for channel in channels if channel is not None]

def fetch_channels():
    BASE_URL = 'https://iptv-org.github.io/api'

    streams_url = f'{BASE_URL}/streams.json'
    channels_url = f'{BASE_URL}/channels.json'
    logos_url = f'{BASE_URL}/logos.json'

    channels = __filter_channels(__request(channels_url))
    streams = __filter_streams(__request(streams_url), channels)
    logos = __filter_logos(__request(logos_url), channels)

    return __channels(channels, streams, logos)
