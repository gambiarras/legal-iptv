from models.channel import Channel

def __group_by_id(channels):
    grouped = {}

    for channel in channels:
        if channel.id not in grouped:
            grouped[channel.id] = []

        grouped[channel.id].append(channel)

    return grouped

def check_channels(channels):
    grouped_by_id = __group_by_id(channels)
    checked_channels = []

    for id in grouped_by_id:
        checked_channels.append(grouped_by_id[id][0])

    return checked_channels
