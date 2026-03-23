from collections import defaultdict

from legal_iptv.clients import HttpClient
from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name


BASE_URL = "https://iptv-org.github.io/api"


def fetch_channels(client: HttpClient) -> list[Channel]:
    channels_raw = client.get_json(f"{BASE_URL}/channels.json")
    streams_raw = client.get_json(f"{BASE_URL}/streams.json")
    logos_raw = client.get_json(f"{BASE_URL}/logos.json")

    br_channels = [
        item for item in channels_raw
        if item.get("country") == "BR" and item.get("is_nsfw") is False
    ]

    valid_channel_ids = {item["id"] for item in br_channels}

    streams_by_channel: dict[str, list[dict]] = defaultdict(list)
    for stream in streams_raw:
        channel_id = stream.get("channel")
        if channel_id in valid_channel_ids:
            streams_by_channel[channel_id].append(stream)

    logos_by_channel: dict[str, str] = {}
    for logo in logos_raw:
        channel_id = logo.get("channel")
        if channel_id in valid_channel_ids and channel_id not in logos_by_channel:
            logos_by_channel[channel_id] = logo.get("url", "")

    result: list[Channel] = []
    for channel in br_channels:
        category = next(iter(channel.get("categories", [])), "general")
        logo_url = logos_by_channel.get(channel["id"], "")

        for stream in streams_by_channel.get(channel["id"], []):
            result.append(
                Channel(
                    id=channel["id"],
                    name=channel["name"],
                    stream_url=stream["url"],
                    logo=logo_url,
                    group=localized_category_name(category),
                    source="iptv_org",
                )
            )

    return result