from datetime import datetime, timedelta, timezone
from typing import Any

from legal_iptv.models.stream_validation import UrlValidation

TERMINAL_OFFLINE_STATUSES = {404, 410}
TEMPORARILY_UNAVAILABLE_STATUS = "temporarily_unavailable"
UNSUPPORTED_PROBE_ERRORS = frozenset({
    "DashSamplingUnsupported", "EncryptedHlsUnsupported", "HlsByteRangeUnsupported",
    "UnsupportedMediaSample", "UnsupportedMediaUrl", "HlsVariantLimit",
    "ProbeByteLimit", "ProbeRequestLimit",
})


def unsupported_probe_error(value: object) -> bool:
    return isinstance(value, str) and value in UNSUPPORTED_PROBE_ERRORS


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, OverflowError):
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    try:
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _activity_status(is_active: bool | None) -> str:
    if is_active is True:
        return "active"
    if is_active is False:
        return "offline"
    return "unknown"


def _previous_failure_count(record: dict[str, Any] | None) -> int:
    if not isinstance(record, dict):
        return 0

    value = record.get("consecutive_failures")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value

    return 0 if record.get("active") is True else 1


def _is_fresh_record(
    record: dict[str, Any], *, max_age_seconds: int, now: datetime | None = None
) -> bool:
    checked_at = _parse_datetime(record.get("checked_at"))
    if checked_at is None:
        return False

    age = (now or _utc_now()) - checked_at
    if not timedelta(0) <= age <= timedelta(seconds=max_age_seconds):
        return False

    active = record.get("active")
    return active is True or active is False or active is None


def offline_urls(
    records: dict[str, dict[str, Any]], *, max_age_seconds: int, now: datetime
) -> set[str]:
    """A retry deadline expiring never converts rejection into recovery.

    ``max_age_seconds`` remains an API-compatible scheduling input; it is not
    permission to republish a known rejected transport without a real success.
    """
    return {
        url
        for url, record in records.items()
        if rejected_record(record, now=now)
    }


def rejected_record(record: dict | None, *, now: datetime) -> bool:
    if not isinstance(record, dict) or record.get("active") is not False:
        return False
    checked = _parse_datetime(record.get("checked_at"))
    status = record.get("status")
    http_status = record.get("http_status")
    return checked is not None and checked <= now and (
        status == "offline" or status == TEMPORARILY_UNAVAILABLE_STATUS
        or type(http_status) is int and http_status in TERMINAL_OFFLINE_STATUSES
    )


def retain_rejected_records(
    previous: dict[str, dict], current: dict[str, dict], *, now: datetime
) -> dict[str, dict]:
    """Keep unobserved rejections when a profile or tombstone omitted a channel.

    Contexts stay independent. The hard limit fails closed instead of silently
    forgetting failures and admitting transports when storage fills up.
    """
    result = dict(current)
    for url, record in previous.items():
        nested = record.get("contexts")
        old = (
            nested if isinstance(nested, dict)
            else {record.get("context_id", "legacy"): record}
        )
        negatives = {
            key: item for key, item in old.items()
            if rejected_record(item, now=now)
        }
        if not negatives:
            continue
        present = result.get(url)
        if present is None:
            result[url] = (
                record if not isinstance(nested, dict)
                else {**record, "contexts": negatives}
            )
            continue
        existing = present.get("contexts")
        members = (
            dict(existing) if isinstance(existing, dict)
            else {present.get("context_id", "legacy"): present}
        )
        for cid, item in negatives.items():
            if cid != "legacy" and cid not in members:
                members[cid] = item
        if len(members) > 1:
            result[url] = {
                "active": None,
                "status": "unknown",
                "validation": "context_summary",
                "contexts": members,
            }
    total = sum(
        len(item["contexts"]) if isinstance(item.get("contexts"), dict) else 1
        for item in result.values()
    )
    if total > 10_000:
        raise ValueError("Stream status context budget exceeded; refusing to forget rejected transports")
    return result


def _record_activity(record: dict[str, Any] | None) -> bool | None:
    if not isinstance(record, dict):
        return False

    active = record.get("active")
    if active is True or active is False or active is None:
        return active

    return False


def _status_record(
    is_active: bool | None,
    *,
    checked_at: str,
    metadata: dict[str, Any] | None = None,
    validation: str = "validated",
    status: str | None = None,
    consecutive_failures: int = 0,
    http_status: int | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    record = {
        "active": is_active,
        "status": status or _activity_status(is_active),
        "checked_at": checked_at,
        "validation": validation,
        "consecutive_failures": consecutive_failures,
        **(metadata or {}),
    }
    if http_status is not None:
        record["http_status"] = http_status
    if error_type:
        record["error_type"] = error_type
    return record


def _validated_status_record(
    result: UrlValidation,
    previous_record: dict[str, Any] | None,
    *,
    checked_at: str,
    metadata: dict[str, Any],
    failure_threshold: int,
    allow_suppression: bool,
) -> dict[str, Any]:
    if result.active is True:
        return _status_record(
            True,
            checked_at=checked_at,
            metadata=metadata,
            consecutive_failures=0,
            http_status=result.http_status,
        )

    unsupported = unsupported_probe_error(result.error_type)
    consecutive_failures = (
        (_previous_failure_count(previous_record) if previous_record else 0)
        + (0 if unsupported else 1)
    )
    if result.active is False and allow_suppression:
        active: bool | None = False
        status = "offline"
    elif rejected_record(previous_record, now=_parse_datetime(checked_at) or _utc_now()):
        active = False
        status = TEMPORARILY_UNAVAILABLE_STATUS
    elif not unsupported and allow_suppression and consecutive_failures >= failure_threshold:
        active = False
        status = TEMPORARILY_UNAVAILABLE_STATUS
    else:
        active = None
        status = "unknown"

    return _status_record(
        active,
        checked_at=checked_at,
        metadata=metadata,
        status=status,
        consecutive_failures=consecutive_failures,
        http_status=result.http_status,
        error_type=result.error_type,
    )
