import json

from importlib import resources

from models.channel import Channel
from models.categories import localized_category_name

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
    with resources.open_text("resources", "extra_channels.json") as file:
        data = json.load(file)
        return __channels(data)
