from collections import defaultdict

from legal_iptv.clients import HttpClient
from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name


BASE_URL = "https://iptv-org.github.io/api"


def fetch_channels(client: HttpClient) -> list[Channel]:
    channels_raw = client.get_json(f"{BASE_URL}/channels.json")
    feeds_raw = client.get_json(f"{BASE_URL}/feeds.json")
    streams_raw = client.get_json(f"{BASE_URL}/streams.json")
    logos_raw = client.get_json(f"{BASE_URL}/logos.json")
    subdivisions_raw = client.get_json(f"{BASE_URL}/subdivisions.json")
    cities_raw = client.get_json(f"{BASE_URL}/cities.json")

    br_channels = [
        item for item in channels_raw
        if item.get("country") == "BR" and item.get("is_nsfw") is False
    ]

    valid_channel_ids = {item["id"] for item in br_channels}
    feeds_by_key = _feeds_by_key(feeds_raw, valid_channel_ids)
    subdivisions_by_code = {
        item["code"]: item
        for item in subdivisions_raw
        if item.get("country") == "BR" and item.get("code")
    }
    cities_by_code = {
        item["code"]: item
        for item in cities_raw
        if item.get("country") == "BR" and item.get("code")
    }

    streams_by_channel: dict[str, list[dict]] = defaultdict(list)
    for stream in streams_raw:
        channel_id = stream.get("channel")
        if channel_id in valid_channel_ids:
            streams_by_channel[channel_id].append(stream)

    logos_by_feed: dict[tuple[str, str | None], str] = {}
    for logo in logos_raw:
        channel_id = logo.get("channel")
        if channel_id not in valid_channel_ids:
            continue

        key = (channel_id, logo.get("feed"))
        if key not in logos_by_feed:
            logos_by_feed[key] = logo.get("url", "")

    result: list[Channel] = []
    for channel in br_channels:
        category = next(iter(channel.get("categories", [])), "general")

        for stream in streams_by_channel.get(channel["id"], []):
            feed_id = stream.get("feed")
            feed = feeds_by_key.get((channel["id"], feed_id))
            logo_url = (
                logos_by_feed.get((channel["id"], feed_id))
                or logos_by_feed.get((channel["id"], None))
                or ""
            )

            result.append(
                Channel(
                    id=channel["id"],
                    name=_channel_name(
                        channel["name"],
                        feed,
                        subdivisions_by_code,
                        cities_by_code,
                    ),
                    stream_url=stream["url"],
                    logo=logo_url,
                    group=localized_category_name(category),
                    source="iptv_org",
                    tvg_id=channel["id"],
                    feed_id=feed_id,
                )
            )

    return result


def _feeds_by_key(
    feeds_raw: list[dict],
    valid_channel_ids: set[str],
) -> dict[tuple[str, str | None], dict]:
    feeds: dict[tuple[str, str | None], dict] = {}
    for feed in feeds_raw:
        channel_id = feed.get("channel")
        if channel_id in valid_channel_ids:
            feeds[(channel_id, feed.get("id"))] = feed

    return feeds


def _channel_name(
    channel_name: str,
    feed: dict | None,
    subdivisions_by_code: dict[str, dict],
    cities_by_code: dict[str, dict],
) -> str:
    if not feed or feed.get("is_main") is True:
        return channel_name

    suffix = _regional_suffix(feed, subdivisions_by_code, cities_by_code)
    if not suffix:
        return channel_name

    if suffix.casefold() in channel_name.casefold():
        return channel_name

    return f"{channel_name} {suffix}"


def _regional_suffix(
    feed: dict,
    subdivisions_by_code: dict[str, dict],
    cities_by_code: dict[str, dict],
) -> str | None:
    for area in feed.get("broadcast_area", []):
        if area.startswith("s/"):
            code = area.removeprefix("s/")
            subdivision = subdivisions_by_code.get(code)
            if subdivision and code.startswith("BR-"):
                return code.removeprefix("BR-")
            if subdivision:
                return subdivision.get("name")

        if area.startswith("ct/"):
            city = cities_by_code.get(area.removeprefix("ct/"))
            if city:
                return _short_city_name(city.get("name", ""))

    return feed.get("name")


def _short_city_name(name: str) -> str | None:
    if not name:
        return None

    if name == "Rio de Janeiro":
        return "Rio"

    return name
