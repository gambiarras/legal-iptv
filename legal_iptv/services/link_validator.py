import logging
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from legal_iptv.clients.http_client import build_headers
from legal_iptv.io import write_json_atomic
from legal_iptv.models import Channel


logger = logging.getLogger(__name__)

FALLBACK_STATUSES = {403, 405}
UNKNOWN_STATUSES = {429}
TRANSIENT_LIVE_SOURCE_TYPES = {"youtube", "twitch", "kick"}

_thread_local = threading.local()


def _is_success_status(status_code: int) -> bool:
    return 200 <= status_code < 400


def _status_to_activity(status_code: int) -> bool | None:
    if _is_success_status(status_code):
        return True

    if status_code in UNKNOWN_STATUSES:
        return None

    return False


def _get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session

    return session


def is_url_active(url: str, timeout: int) -> bool | None:
    headers = build_headers(accept="*/*")
    session = _get_session()

    try:
        response = session.head(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )

        if _is_success_status(response.status_code):
            return True

        if response.status_code not in FALLBACK_STATUSES:
            return _status_to_activity(response.status_code)

    except requests.RequestException:
        pass

    try:
        response = session.get(
            url,
            headers={**headers, "Range": "bytes=0-0"},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        try:
            return _status_to_activity(response.status_code)
        finally:
            response.close()

    except requests.RequestException:
        return False


def validate_urls(
    urls: list[str],
    *,
    max_workers: int,
    timeout: int,
) -> dict[str, bool | None]:
    if not urls:
        return {}

    worker_count = max(1, min(max_workers, len(urls)))
    status_by_url: dict[str, bool | None] = {}

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(is_url_active, url, timeout): url
            for url in urls
        }

        for future in as_completed(futures):
            url = futures[future]
            try:
                status_by_url[url] = future.result()
            except Exception as exc:
                logger.warning("Stream validation failed url=%s error=%s", url, exc)
                status_by_url[url] = False

    return status_by_url


def filter_active_channels(
    channels: list[Channel],
    *,
    max_workers: int,
    timeout: int,
) -> list[Channel]:
    urls = sorted({channel.stream_url for channel in channels if channel.stream_url})
    status_by_url = validate_urls(
        urls,
        max_workers=max_workers,
        timeout=timeout,
    )

    active_channels = [
        channel
        for channel in channels
        if channel.stream_url and status_by_url.get(channel.stream_url) is not False
    ]
    unknown_count = sum(1 for is_active in status_by_url.values() if is_active is None)

    logger.info(
        "Stream validation finished total=%s kept=%s inactive=%s unknown_urls=%s unique_urls=%s",
        len(channels),
        len(active_channels),
        len(channels) - len(active_channels),
        unknown_count,
        len(urls),
    )

    return active_channels


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def write_stream_status(
    status_file: Path,
    status_by_url: dict[str, bool | None],
) -> None:
    checked_at = _utc_now().isoformat()
    payload = {
        "generated_at": checked_at,
        "urls": {
            url: {
                "active": is_active,
                "checked_at": checked_at,
            }
            for url, is_active in sorted(status_by_url.items())
        },
    }

    write_json_atomic(status_file, payload)


def load_offline_urls(
    status_file: Path,
    *,
    max_age_seconds: int,
) -> set[str]:
    if not status_file.exists():
        return set()

    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read stream status file path=%s", status_file)
        return set()

    url_statuses = payload.get("urls", {})
    if not isinstance(url_statuses, dict):
        return set()

    cutoff = _utc_now() - timedelta(seconds=max_age_seconds)
    offline_urls: set[str] = set()

    for url, status in url_statuses.items():
        if not isinstance(status, dict):
            continue

        checked_at = _parse_datetime(status.get("checked_at"))
        if checked_at is None or checked_at < cutoff:
            continue

        if status.get("active") is False:
            offline_urls.add(url)

    return offline_urls


def filter_cached_offline_channels(
    channels: list[Channel],
    *,
    status_file: Path,
    max_age_seconds: int,
) -> list[Channel]:
    offline_urls = load_offline_urls(
        status_file,
        max_age_seconds=max_age_seconds,
    )

    if not offline_urls:
        return channels

    filtered_channels = [
        channel
        for channel in channels
        if channel.stream_url not in offline_urls or _is_transient_live_channel(channel)
    ]

    logger.info(
        "Cached stream status filter finished total=%s kept=%s skipped=%s offline_urls=%s",
        len(channels),
        len(filtered_channels),
        len(channels) - len(filtered_channels),
        len(offline_urls),
    )

    return filtered_channels


def _is_transient_live_channel(channel: Channel) -> bool:
    return (
        channel.source == "live_stream_catalog"
        and channel.source_type in TRANSIENT_LIVE_SOURCE_TYPES
    )


def _transient_live_urls(channels: list[Channel]) -> set[str]:
    return {
        channel.stream_url
        for channel in channels
        if channel.stream_url and _is_transient_live_channel(channel)
    }


def refresh_stream_status(
    channels: list[Channel],
    *,
    status_file: Path,
    max_workers: int,
    timeout: int,
) -> list[Channel]:
    urls = sorted({channel.stream_url for channel in channels if channel.stream_url})
    status_by_url = validate_urls(
        urls,
        max_workers=max_workers,
        timeout=timeout,
    )
    for url in _transient_live_urls(channels):
        if status_by_url.get(url) is False:
            status_by_url[url] = None

    write_stream_status(status_file, status_by_url)

    active_channels = [
        channel
        for channel in channels
        if channel.stream_url and status_by_url.get(channel.stream_url) is not False
    ]
    unknown_count = sum(1 for is_active in status_by_url.values() if is_active is None)

    logger.info(
        "Stream status refreshed total=%s kept=%s inactive=%s unknown_urls=%s unique_urls=%s status_file=%s",
        len(channels),
        len(active_channels),
        len(channels) - len(active_channels),
        unknown_count,
        len(urls),
        status_file,
    )

    return active_channels
