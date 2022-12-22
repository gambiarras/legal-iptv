from urllib.request import urlopen

import json

def filter_channels(channels):
    return [
        channel for channel in channels
            if channel['country'] == 'BR' and channel['is_nsfw'] is False
    ]

def filter_streams(streams, channels):
    return [
        stream for stream in streams
            if 'status' in stream 
                and stream['status'] != 'error' and stream['status'] != 'blocked'
                and [channel for channel in channels if channel['id'] == stream['channel']]
    ]

def main():
    BASE_URL = 'https://iptv-org.github.io/api'

    categories_url = f'{BASE_URL}/categories.json'
    streams_url = f'{BASE_URL}/streams.json'
    channels_url = f'{BASE_URL}/channels.json'

    channels = filter_channels(json.load(urlopen(channels_url)))
    streams = filter_streams(json.load(urlopen(streams_url)), channels)

    print(channels)

main()