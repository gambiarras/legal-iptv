import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from legal_iptv.clients.http_client import build_headers
from legal_iptv.models import Channel
from legal_iptv.io.stream_status_store import (
    _read_status_records,
    _write_stream_status_records,
)
from legal_iptv.models.stream_validation import UrlValidation
from legal_iptv.services.stream_status import (
    TEMPORARILY_UNAVAILABLE_STATUS,
    TERMINAL_OFFLINE_STATUSES,
    _activity_status,
    _is_fresh_record,
    _record_activity,
    _status_record,
    _utc_now,
    _validated_status_record,
)
from legal_iptv.services.extra_removal import update_extra_removals


logger = logging.getLogger(__name__)

FALLBACK_STATUSES = {403, 404, 405, 410}
TRANSIENT_LIVE_SOURCE_TYPES = {"youtube", "twitch", "kick"}

_thread_local = threading.local()


def _is_success_status(status_code: int) -> bool:
    return 200 <= status_code < 400


def _status_to_activity(status_code: int) -> bool | None:
    if _is_success_status(status_code):
        return True

    if status_code in TERMINAL_OFFLINE_STATUSES:
        return False

    return None


def _get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session

    return session


def inspect_url(url: str, timeout: int) -> UrlValidation:
    headers = build_headers(accept="*/*")
    session = _get_session()
    head_status: int | None = None

    try:
        response = session.head(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        head_status = response.status_code

        if _is_success_status(head_status):
            return UrlValidation(True, head_status)

        if head_status not in FALLBACK_STATUSES:
            return UrlValidation(
                _status_to_activity(head_status),
                head_status,
            )

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
            return UrlValidation(
                _status_to_activity(response.status_code),
                response.status_code,
            )
        finally:
            response.close()

    except requests.RequestException as exc:
        if head_status is not None:
            return UrlValidation(
                _status_to_activity(head_status),
                head_status,
                type(exc).__name__,
            )
        return UrlValidation(None, error_type=type(exc).__name__)


def is_url_active(url: str, timeout: int) -> bool | None:
    return inspect_url(url, timeout).active


def validate_url_details(
    urls: list[str],
    *,
    max_workers: int,
    timeout: int,
) -> dict[str, UrlValidation]:
    if not urls:
        return {}

    worker_count = max(1, min(max_workers, len(urls)))
    results: dict[str, UrlValidation] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(inspect_url, url, timeout): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as exc:
                logger.warning(
                    "Stream validation failed error_type=%s",
                    type(exc).__name__,
                )
                results[url] = UrlValidation(None, error_type=type(exc).__name__)
    return results


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
                logger.warning(
                    "Stream validation failed error_type=%s",
                    type(exc).__name__,
                )
                status_by_url[url] = None

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


def _url_metadata(channels: list[Channel]) -> dict[str, dict]:
    metadata: dict[str, dict] = {}

    for channel in channels:
        if not channel.stream_url:
            continue

        item = metadata.setdefault(
            channel.stream_url,
            {
                "channels": [],
                "sources": set(),
                "source_types": set(),
            },
        )
        item["channels"].append(
            {
                "id": channel.id,
                "name": channel.name,
                "source": channel.source,
                "source_type": channel.source_type,
            }
        )
        item["sources"].add(channel.source)
        if channel.source_type:
            item["source_types"].add(channel.source_type)

    return {
        url: {
            "channels": item["channels"],
            "sources": sorted(item["sources"]),
            "source_types": sorted(item["source_types"]),
        }
        for url, item in metadata.items()
    }


def write_stream_status(
    status_file: Path,
    status_by_url: dict[str, bool | None],
) -> None:
    checked_at = _utc_now().isoformat()
    records = {
        url: _status_record(is_active, checked_at=checked_at)
        for url, is_active in status_by_url.items()
    }
    _write_stream_status_records(
        status_file,
        records,
        generated_at=checked_at,
        validated_urls=len(status_by_url),
        cached_urls=0,
    )


def load_offline_urls(
    status_file: Path,
    *,
    max_age_seconds: int,
) -> set[str]:
    records = _read_status_records(status_file)
    offline_urls: set[str] = set()

    for url, record in records.items():
        if (
            record.get("active") is False
            and record.get("status") == TEMPORARILY_UNAVAILABLE_STATUS
        ):
            offline_urls.add(url)
            continue

        if not _is_fresh_record(record, max_age_seconds=max_age_seconds):
            continue

        if (
            record.get("active") is False
            and record.get("http_status") in TERMINAL_OFFLINE_STATUSES
        ):
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
    max_age_seconds: int,
    failure_threshold: int = 2,
    extra_removed_file: Path | None = None,
) -> list[Channel]:
    failure_threshold = max(1, failure_threshold)
    urls = sorted({channel.stream_url for channel in channels if channel.stream_url})
    previous_records = _read_status_records(status_file)
    fresh_records = {
        url: previous_records[url]
        for url in urls
        if url in previous_records
        and _is_fresh_record(previous_records[url], max_age_seconds=max_age_seconds)
        and previous_records[url].get("active") is True
    }
    urls_to_validate = [url for url in urls if url not in fresh_records]

    validation_details = validate_url_details(
        urls_to_validate,
        max_workers=max_workers,
        timeout=timeout,
    )
    http_status_by_url = {
        url: result.http_status
        for url, result in validation_details.items()
    }
    if extra_removed_file is not None:
        update_extra_removals(channels, http_status_by_url, extra_removed_file)

    checked_at = _utc_now().isoformat()
    metadata_by_url = _url_metadata(channels)
    records_by_url: dict[str, dict] = {}
    transient_live_urls = _transient_live_urls(channels)

    for url in urls:
        metadata = metadata_by_url.get(url, {})
        if url in validation_details:
            records_by_url[url] = _validated_status_record(
                validation_details[url],
                previous_records.get(url),
                checked_at=checked_at,
                metadata=metadata,
                failure_threshold=failure_threshold,
                allow_suppression=url not in transient_live_urls,
            )
            continue

        cached_record = dict(fresh_records[url])
        cached_record.update(metadata)
        cached_record["status"] = _activity_status(cached_record.get("active"))
        cached_record["validation"] = "cached"
        records_by_url[url] = cached_record

    _write_stream_status_records(
        status_file,
        records_by_url,
        generated_at=checked_at,
        validated_urls=len(urls_to_validate),
        cached_urls=len(fresh_records),
    )

    active_channels = [
        channel
        for channel in channels
        if channel.stream_url
        and _record_activity(records_by_url.get(channel.stream_url)) is not False
    ]
    unknown_count = sum(
        1 for record in records_by_url.values()
        if _record_activity(record) is None
    )

    logger.info(
        "Stream status refreshed total=%s kept=%s inactive=%s unknown_urls=%s unique_urls=%s validated_urls=%s cached_urls=%s status_file=%s",
        len(channels),
        len(active_channels),
        len(channels) - len(active_channels),
        unknown_count,
        len(urls),
        len(urls_to_validate),
        len(fresh_records),
        status_file,
    )

    return active_channels
