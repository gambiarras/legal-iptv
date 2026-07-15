import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from legal_iptv.clients import HttpClient
from legal_iptv.io import write_json_atomic
from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import localized_category_name


BASE_URL = "https://iptv-org.github.io/api"
API_FILES = (
    "channels",
    "feeds",
    "streams",
    "logos",
    "subdivisions",
    "cities",
)

logger = logging.getLogger(__name__)


def fetch_channels(
    client: HttpClient,
    *,
    cache_file: Path | None = None,
    cache_ttl_seconds: int = 43200,
    force_refresh: bool = False,
) -> list[Channel]:
    if cache_file is not None and not force_refresh:
        cached_channels = _load_fresh_channels_cache(
            cache_file,
            max_age_seconds=cache_ttl_seconds,
        )
        if cached_channels is not None:
            logger.info(
                "Loaded IPTV-org channels from cache channels=%s cache_file=%s",
                len(cached_channels),
                cache_file,
            )
            return cached_channels

    try:
        channels = _build_channels(_fetch_api_data(client))
    except Exception:
        if cache_file is not None:
            stale_channels = _load_channels_cache(cache_file, max_age_seconds=None)
            if stale_channels is not None:
                logger.warning(
                    "Using stale IPTV-org channel cache channels=%s cache_file=%s",
                    len(stale_channels),
                    cache_file,
                )
                return stale_channels
        raise

    if cache_file is not None:
        _write_channels_cache(cache_file, channels)

    return channels


def _build_channels(raw: dict[str, list[dict]]) -> list[Channel]:
    channels_raw = raw["channels"]
    feeds_raw = raw["feeds"]
    streams_raw = raw["streams"]
    logos_raw = raw["logos"]
    subdivisions_raw = raw["subdivisions"]
    cities_raw = raw["cities"]

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


def _fetch_api_data(client: HttpClient) -> dict[str, list[dict]]:
    payload: dict[str, list[dict]] = {}
    for name in API_FILES:
        data = client.get_json(f"{BASE_URL}/{name}.json")
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected IPTV-org API payload: {name}")
        payload[name] = data

    logger.info("Loaded IPTV-org API data from sources files=%s", len(payload))
    return payload


def _load_fresh_channels_cache(
    cache_file: Path,
    *,
    max_age_seconds: int,
) -> list[Channel] | None:
    return _load_channels_cache(cache_file, max_age_seconds=max_age_seconds)


def _load_channels_cache(
    cache_file: Path,
    *,
    max_age_seconds: int | None,
) -> list[Channel] | None:
    if not cache_file.exists():
        return None

    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read IPTV-org cache cache_file=%s", cache_file)
        return None

    generated_at = _parse_datetime(payload.get("generated_at"))
    if generated_at is None:
        return None

    if max_age_seconds is not None:
        cutoff = _utc_now() - timedelta(seconds=max_age_seconds)
        if generated_at < cutoff:
            return None

    raw_channels = payload.get("channels")
    if not isinstance(raw_channels, list):
        return None

    channels: list[Channel] = []
    for item in raw_channels:
        if not isinstance(item, dict):
            continue

        try:
            channels.append(Channel(**item))
        except TypeError:
            logger.warning("Skipping invalid IPTV-org cached channel cache_file=%s", cache_file)

    return channels


def _write_channels_cache(cache_file: Path, channels: list[Channel]) -> None:
    write_json_atomic(
        cache_file,
        {
            "generated_at": _utc_now().isoformat(),
            "channels": [channel.to_dict() for channel in channels],
        },
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


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
