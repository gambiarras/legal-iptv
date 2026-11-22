import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from legal_iptv.clients.http_client import build_headers
from legal_iptv.io.stream_status_store import (
    _read_status_records,
    _write_stream_status_records,
    read_stream_status,
    status_records,
)
from legal_iptv.models import Channel
from legal_iptv.models.stream_validation import UrlValidation
from legal_iptv.services.extra_removal import update_extra_removals
from legal_iptv.services.stream_status import (
    TEMPORARILY_UNAVAILABLE_STATUS,
    _parse_datetime,
    _record_activity,
    _status_record,
    _utc_now,
    _validated_status_record,
    offline_urls,
    rejected_record,
    retain_rejected_records,
)
from legal_iptv.services.transport_probe import PROBE_VERSION, probe_transport
from legal_iptv.services.validation_metrics import (
    build_validation_metrics,
    safe_metric_label,
    transport_id,
)
from legal_iptv.services.validation_schedule import (
    ValidationContext,
    ValidationPolicy,
    can_reuse,
    context_for_channel,
    matching_record,
    previous_observation,
    schedule_record,
)

logger = logging.getLogger(__name__)

_thread_local = threading.local()
MAX_PROBE_WORKERS = 32


class StreamProbeSession(requests.Session):
    """Keep transport credentials off other origins during redirects."""

    def __init__(self):
        super().__init__()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4, pool_maxsize=1, max_retries=0, pool_block=True
        )
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def rebuild_auth(
        self, prepared_request: requests.PreparedRequest, response: requests.Response
    ) -> None:
        super().rebuild_auth(prepared_request, response)
        before = urlsplit(response.request.url)
        after = urlsplit(prepared_request.url)
        if (before.scheme, before.hostname, before.port) != (
            after.scheme,
            after.hostname,
            after.port,
        ):
            safe = {
                "accept",
                "accept-encoding",
                "accept-language",
                "connection",
                "user-agent",
                "range",
            }
            for name in list(prepared_request.headers):
                if name.casefold() not in safe:
                    del prepared_request.headers[name]


def _get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = StreamProbeSession()
        _thread_local.session = session

    return session


def _initialize_probe_thread(sessions: list[requests.Session]) -> None:
    session = StreamProbeSession()
    _thread_local.session = session
    sessions.append(session)


def inspect_url(
    url: str, timeout: int, request_headers: dict[str, str] | None = None
) -> UrlValidation:
    headers = build_headers(accept="*/*")
    headers = {name.casefold(): value for name, value in headers.items()}
    if request_headers:
        headers.update({name.casefold(): value for name, value in request_headers.items()})
    return probe_transport(_get_session(), url, timeout, headers)


def is_url_active(url: str, timeout: int) -> bool | None:
    return inspect_url(url, timeout).active


def validate_url_details(
    urls: list[str],
    *,
    max_workers: int,
    timeout: int,
    request_headers_by_url: dict[str, dict[str, str]] | None = None,
) -> dict[str, UrlValidation]:
    if not urls:
        return {}

    worker_count = max(1, min(max_workers, len(urls), MAX_PROBE_WORKERS))
    results: dict[str, UrlValidation] = {}
    sessions: list[requests.Session] = []
    try:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            initializer=_initialize_probe_thread,
            initargs=(sessions,),
        ) as executor:
            futures = {
                executor.submit(
                    inspect_url, url, timeout, (request_headers_by_url or {}).get(url)
                ): url
                for url in urls
            }
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
    finally:
        for session in sessions:
            session.close()
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
        futures = {executor.submit(is_url_active, url, timeout): url for url in urls}

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
                "provider_ids": set(),
            },
        )
        item["channels"].append(
            {
                "id": channel.id,
                "name": channel.name,
                "source": channel.source,
                "source_type": channel.source_type,
                "provider_id": channel.provider_id
                or channel.source_type
                or channel.source,
                "logical_channel_id": channel.logical_channel_id or channel.id,
                "feed_id": channel.feed_id,
            }
        )
        item["sources"].add(channel.source)
        if channel.source_type:
            item["source_types"].add(channel.source_type)
        item["provider_ids"].add(
            safe_metric_label(
                channel.provider_id or channel.source_type or channel.source
            )
        )

    return {
        url: {
            "transport_id": transport_id(url),
            "channels": item["channels"],
            "sources": sorted(item["sources"]),
            "source_types": sorted(item["source_types"]),
            "provider_ids": sorted(item["provider_ids"]),
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
    return offline_urls(
        _read_status_records(status_file),
        max_age_seconds=max_age_seconds,
        now=_utc_now(),
    )


def filter_cached_offline_channels(
    channels: list[Channel],
    *,
    status_file: Path,
    max_age_seconds: int,
) -> list[Channel]:
    records = _read_status_records(status_file)
    now = _utc_now()
    filtered_channels = []
    for channel in channels:
        context = context_for_channel(channel)
        if context.expired(now):
            continue
        record = records.get(channel.stream_url)
        matched = matching_record(record, context)
        # Legacy suppression is only safe for transports without declared headers.
        if (
            matched is None
            and record
            and "context_id" not in record
            and "contexts" not in record
            and not channel.request_headers
        ):
            matched = record
        suppressed = offline_urls(
            {channel.stream_url: matched} if matched else {},
            max_age_seconds=max_age_seconds,
            now=now,
        )
        if not suppressed:
            filtered_channels.append(channel)

    logger.info(
        "Cached stream status filter finished total=%s kept=%s skipped=%s offline_urls=%s",
        len(channels),
        len(filtered_channels),
        len(channels) - len(filtered_channels),
        len(channels) - len(filtered_channels),
    )

    return filtered_channels


def _media_success_at(record: dict | None) -> datetime | None:
    if not isinstance(record, dict) or (
        record.get("probe_version") != PROBE_VERSION
        or record.get("validation_level") != "transport_sample"
    ):
        return None
    value = record.get("last_media_success_at")
    if record.get("active") is True:
        # Version 2 sampled records predate the explicit last-success field.
        value = record.get("checked_at")
    return _parse_datetime(value)


def media_record_is_confirmed(record: dict | None, *, now: datetime,
                              max_age_seconds: int = 86400) -> bool:
    """Apply the shared positive-media policy to an already matched exact context."""
    if not isinstance(record, dict) or record.get("active") is False or rejected_record(record, now=now):
        return False
    if record.get("status") != ("active" if record.get("active") is True else "unknown"):
        return False
    checked = _parse_datetime(record.get("checked_at"))
    success = _media_success_at(record)
    if checked is None or checked > now or success is None:
        return False
    if not 0 <= (now - success).total_seconds() <= max_age_seconds:
        return False
    failures = record.get("consecutive_failures", 0)
    if record.get("active") is None and (type(failures) is not int or not 0 <= failures <= 1):
        return False
    return record.get("active") is True or record.get("active") is None


def filter_media_confirmed_channels(
    channels: list[Channel],
    *,
    status_file: Path,
    max_age_seconds: int = 86400,
) -> list[Channel]:
    """Publish only recent sampled media or one bounded inconclusive retry.

    The ordinary negative-cache API remains permissive for legacy consumers;
    production selection can explicitly require this positive proof instead.
    This function performs no requests and never changes observation timestamps.
    """
    records = _read_status_records(status_file)
    now = _utc_now()
    confirmed: list[Channel] = []
    for channel in channels:
        context = context_for_channel(channel)
        if context.expired(now):
            continue
        record = matching_record(records.get(context.url), context)
        if media_record_is_confirmed(record, now=now, max_age_seconds=max_age_seconds):
            confirmed.append(channel)
    logger.info(
        "Media proof filter finished total=%s confirmed=%s unconfirmed=%s",
        len(channels), len(confirmed), len(channels) - len(confirmed),
    )
    return confirmed


def _validate_contexts(
    contexts: dict[str, ValidationContext],
    *,
    max_workers: int,
    timeout: int,
) -> dict[str, UrlValidation]:
    """Batch distinct URLs while probing separate contexts for shared URLs."""
    batches: list[dict[str, ValidationContext]] = []
    counts: dict[str, int] = {}
    for context in contexts.values():
        index = counts.get(context.url, 0)
        if index == len(batches):
            batches.append({})
        batches[index][context.url] = context
        counts[context.url] = index + 1
    results: dict[str, UrlValidation] = {}
    for batch in batches:
        header_options = (
            {
                "request_headers_by_url": {
                    url: context.headers for url, context in batch.items()
                }
            }
            if any(context.headers for context in batch.values())
            else {}
        )
        observed = validate_url_details(
            sorted(batch),
            max_workers=max_workers,
            timeout=timeout,
            **header_options,
        )
        for url, context in batch.items():
            results[context.id] = observed.get(
                url, UrlValidation(None, error_type="MissingProbeResult")
            )
    return results


def _url_summaries(
    contexts: dict[str, ValidationContext],
    records: dict[str, dict],
    metadata: dict[str, dict],
) -> dict[str, dict]:
    """Preserve the URL reader contract without merging context observations."""
    grouped: dict[str, dict[str, dict]] = {}
    for cid, context in contexts.items():
        grouped.setdefault(context.url, {})[cid] = records[cid]
    summaries = {}
    for url, members in grouped.items():
        if len(members) == 1:
            summaries[url] = next(iter(members.values()))
            continue
        # A mixed URL summary cannot suppress all its contexts in legacy readers.
        summaries[url] = {
            **metadata[url],
            "active": None,
            "status": "unknown",
            "validation": "context_summary",
            "contexts": members,
        }
    return summaries


def refresh_stream_status(
    channels: list[Channel],
    *,
    status_file: Path,
    max_workers: int,
    timeout: int,
    max_age_seconds: int,
    failure_threshold: int = 2,
    extra_removed_file: Path | None = None,
    policy: ValidationPolicy | None = None,
    force: bool = False,
) -> list[Channel]:
    failure_threshold = max(1, failure_threshold)
    policy = policy or ValidationPolicy(base_seconds=max_age_seconds)
    channel_contexts = [
        (channel, context_for_channel(channel))
        for channel in channels
        if channel.stream_url
    ]
    contexts = {context.id: context for channel, context in channel_contexts}
    channels_by_context: dict[str, list[Channel]] = {cid: [] for cid in contexts}
    for channel, context in channel_contexts:
        channels_by_context[context.id].append(channel)
    previous_snapshot = read_stream_status(status_file)
    previous_records = status_records(previous_snapshot)
    freshness_now = _utc_now()
    previous_by_context = {
        cid: previous_observation(
            previous_records.get(context.url), context, policy, now=freshness_now
        )
        for cid, context in contexts.items()
    }
    for cid, context in contexts.items():
        saved = previous_records.get(context.url)
        raw = matching_record(saved, context)
        if (
            raw is None and saved and not context.headers
            and "context_id" not in saved and "contexts" not in saved
        ):
            raw = saved
        if rejected_record(raw, now=freshness_now):
            previous_by_context[cid] = raw
        elif previous_by_context[cid] and previous_by_context[cid].get("probe_version") != PROBE_VERSION:
            # A legacy HEAD success is not transport evidence or a success streak.
            previous_by_context[cid] = None
    fresh_records = {
        cid: dict(previous_by_context[cid])
        for cid, context in contexts.items()
        if previous_by_context[cid]
        and previous_by_context[cid].get("probe_version") == PROBE_VERSION
        and can_reuse(previous_by_context[cid], context, policy, now=freshness_now, force=force)
    }
    pending = {
        cid: context
        for cid, context in contexts.items()
        if cid not in fresh_records and not context.expired(freshness_now)
    }
    validation_details = _validate_contexts(
        pending,
        max_workers=max_workers,
        timeout=timeout,
    )
    if extra_removed_file is not None:
        update_extra_removals(
            channels,
            {},
            extra_removed_file,
            validation_by_channel={
                channel.id: result
                for cid, result in validation_details.items()
                for channel in channels_by_context[cid]
                if channel.source == "extra"
            },
        )

    checked_now = _utc_now()
    checked_at = checked_now.isoformat()
    metadata_by_url = _url_metadata(channels)
    metadata_by_context = {
        cid: {
            **_url_metadata(members)[contexts[cid].url],
            "transport_id": transport_id(cid),
        }
        for cid, members in channels_by_context.items()
    }
    records_by_context: dict[str, dict] = {}
    for cid, context in contexts.items():
        metadata = metadata_by_context[cid]
        if cid in validation_details:
            result = validation_details[cid]
            record = _validated_status_record(
                result,
                previous_by_context[cid],
                checked_at=checked_at,
                metadata=metadata,
                failure_threshold=failure_threshold,
                allow_suppression=True,
            )
            record["probe_version"] = PROBE_VERSION
            record["validation_level"] = "transport_sample"
            if result.active is True:
                record["last_media_success_at"] = checked_at
            elif result.active is None:
                previous = matching_record(previous_records.get(context.url), context)
                last_success = _media_success_at(previous)
                if last_success is not None and last_success <= checked_now:
                    record["last_media_success_at"] = last_success.isoformat()
            records_by_context[cid] = schedule_record(
                record,
                previous_by_context[cid],
                context,
                policy,
                now=checked_now,
                active=result.active,
            )
            continue
        if cid in fresh_records:
            records_by_context[cid] = {
                **fresh_records[cid],
                **metadata,
                "validation": "cached",
            }
        else:
            records_by_context[cid] = {
                **metadata,
                "context_id": cid,
                "active": None,
                "status": "unknown",
                "validation": "expired",
            }

    records_by_url = retain_rejected_records(
        previous_records,
        _url_summaries(contexts, records_by_context, metadata_by_url),
        now=checked_now,
    )

    metrics = build_validation_metrics(
        previous_snapshot,
        validation_details,
        metadata_by_context,
        now=checked_now,
        cached_urls=len(fresh_records),
        suppressed_urls=sum(
            record.get("status") == TEMPORARILY_UNAVAILABLE_STATUS
            for record in records_by_context.values()
        ),
    )
    metrics.summary["cache_unit"] = "transport_context"
    metrics.summary["validation_level"] = "transport_sample"
    metrics.summary["probe_version"] = PROBE_VERSION
    _write_stream_status_records(
        status_file,
        records_by_url,
        generated_at=checked_at,
        validated_urls=len({context.url for context in pending.values()}),
        cached_urls=len(
            {context.url for cid, context in contexts.items() if cid in fresh_records}
            - {context.url for context in pending.values()}
        ),
        history=metrics.history,
        validation_metrics=metrics.summary,
        top_failing_channels=metrics.top_failing_channels,
        provider_metrics=metrics.providers,
        scheduling_summary={
            "total_contexts": len(contexts),
            "validated_contexts": len(pending),
            "cached_contexts": len(fresh_records),
            "expired_contexts": len(contexts) - len(pending) - len(fresh_records),
        },
    )

    active_channels = [
        channel
        for channel, context in channel_contexts
        if not context.expired(checked_now)
        and _record_activity(records_by_context.get(context.id)) is not False
    ]
    unknown_count = sum(
        1 for record in records_by_context.values() if _record_activity(record) is None
    )

    logger.info(
        "Stream status refreshed total=%s kept=%s inactive=%s unknown_urls=%s unique_urls=%s validated_urls=%s cached_urls=%s status_file=%s",
        len(channels),
        len(active_channels),
        len(channels) - len(active_channels),
        unknown_count,
        len(records_by_url),
        len(pending),
        len(fresh_records),
        status_file,
    )

    return active_channels
