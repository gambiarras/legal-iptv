from datetime import datetime, timedelta, timezone
from typing import Any

from legal_iptv.models.stream_validation import UrlValidation

TERMINAL_OFFLINE_STATUSES = {404, 410}
TEMPORARILY_UNAVAILABLE_STATUS = "temporarily_unavailable"


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
    """Apply cached suppression only while the observation is still fresh."""
    return {
        url
        for url, record in records.items()
        if _is_fresh_record(record, max_age_seconds=max_age_seconds, now=now)
        and record.get("active") is False
        and (
            record.get("status") == TEMPORARILY_UNAVAILABLE_STATUS
            or (
                type(record.get("http_status")) is int
                and record["http_status"] in TERMINAL_OFFLINE_STATUSES
            )
        )
    }


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

    consecutive_failures = _previous_failure_count(previous_record) + 1
    if result.active is False and allow_suppression:
        active: bool | None = False
        status = "offline"
    elif allow_suppression and consecutive_failures >= failure_threshold:
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
