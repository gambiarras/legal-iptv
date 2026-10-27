"""Pure scheduling decisions for exact stream contexts, without network or IO."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from legal_iptv.models import Channel
from legal_iptv.services.stream_status import _parse_datetime

POLICY_VERSION = 1
TRANSIENT_TYPES = {"youtube", "twitch", "kick"}


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Bound successful reuse separately from negative observation freshness."""

    base_seconds: int = 14400
    max_success_seconds: int = 86400
    retry_seconds: int = 900

    def __post_init__(self) -> None:
        if min(self.base_seconds, self.max_success_seconds, self.retry_seconds) < 1:
            raise ValueError("Validation intervals must be positive")
        if (
            max(self.base_seconds, self.max_success_seconds, self.retry_seconds)
            > 31536000
        ):
            raise ValueError("Validation intervals cannot exceed one year")

    def interval(self, success: bool, streak: int, *, transient: bool) -> int:
        """Increase success intervals gradually and bound unsuccessful retries."""
        if success:
            multiplier = (1, 2, 3, 6)[min(max(streak, 1), 4) - 1]
            ceiling = (
                min(self.base_seconds, self.max_success_seconds)
                if transient
                else self.max_success_seconds
            )
            return min(self.base_seconds * multiplier, ceiling)
        return min(
            self.retry_seconds * (1 << min(max(streak - 1, 0), 16)), self.base_seconds
        )


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """In-memory probe inputs; only the fingerprint is persisted."""

    id: str
    url: str
    headers: dict[str, str]
    expires_at: datetime | None
    invalid_expiry: bool
    transient: bool

    def expired(self, now: datetime) -> bool:
        """Reject expired or malformed explicit validity declarations."""
        return self.invalid_expiry or (
            self.expires_at is not None and self.expires_at <= now
        )


def context_for_channel(channel: Channel) -> ValidationContext:
    """Fingerprint declared probe inputs, without storing raw header values."""
    headers = {
        name.casefold(): value for name, value in channel.request_headers.items()
    }
    transient = (
        channel.source == "live_stream_catalog"
        and channel.source_type in TRANSIENT_TYPES
    )
    expiry = _parse_datetime(channel.expires_at)
    invalid = bool(channel.expires_at) and expiry is None
    # Ingestion recalculates ttl_seconds as remaining time when expires_at exists.
    # Only an absolute declaration is stable across successive catalog loads.
    if channel.expires_at is None and channel.ttl_seconds is not None:
        resolved = _parse_datetime(channel.resolved_at)
        if (
            resolved is not None
            and type(channel.ttl_seconds) is int
            and channel.ttl_seconds > 0
        ):
            try:
                expiry = resolved + timedelta(seconds=channel.ttl_seconds)
            except OverflowError:
                invalid = True
        else:
            invalid = True
    inputs = [
        POLICY_VERSION,
        channel.stream_url,
        sorted(headers.items()),
        channel.protocol,
        channel.resolution,
        channel.codec,
        channel.bitrate,
        channel.delivery_mode,
        channel.requires_dynamic_resolution,
        transient,
        sorted(channel.secret_refs.items()),
        expiry.isoformat() if expiry else None,
        invalid,
    ]
    fingerprint = hashlib.sha256(
        json.dumps(inputs, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return ValidationContext(
        fingerprint, channel.stream_url, headers, expiry, invalid, transient
    )


def matching_record(
    record: dict[str, Any] | None, context: ValidationContext
) -> dict[str, Any] | None:
    """Read a matching flat record or a context within a shared URL summary."""
    if not record:
        return None
    nested = record.get("contexts")
    if isinstance(nested, dict):
        record = nested.get(context.id)
    if isinstance(record, dict) and record.get("context_id") == context.id:
        return record
    return None


def _valid_observation(record: dict[str, Any]) -> bool:
    """Reject incomplete or contradictory state instead of filtering by it."""
    if "active" not in record or "last_probe_active" not in record:
        return False
    active = record["active"]
    observed = record["last_probe_active"]
    status = record.get("status")
    if observed is True:
        return active is True and status == "active"
    if observed is False:
        return (active is False and status == "offline") or (
            active is None and status == "unknown"
        )
    if observed is None:
        return (active is None and status == "unknown") or (
            active is False and status == "temporarily_unavailable"
        )
    return False


def can_reuse(
    record: dict[str, Any] | None,
    context: ValidationContext,
    policy: ValidationPolicy,
    *,
    now: datetime,
    force: bool = False,
) -> bool:
    """Reuse only a valid, matching, unexpired observation before its deadline."""
    if (
        force
        or not record
        or record.get("context_id") != context.id
        or record.get("schedule_version") != POLICY_VERSION
        or context.expired(now)
        or not _valid_observation(record)
    ):
        return False
    checked = _parse_datetime(record.get("checked_at"))
    deadline = _parse_datetime(record.get("next_check_at"))
    success = record.get("last_probe_active")
    if (
        checked is None
        or deadline is None
        or checked > now
        or deadline <= now
        or deadline <= checked
    ):
        return False
    streak = record.get(
        "consecutive_successes" if success is True else "consecutive_failures"
    )
    if type(streak) is not int or streak < 1 or "last_probe_active" not in record:
        return False
    cap = policy.interval(success is True, streak, transient=context.transient)
    return (now - checked).total_seconds() < cap and (
        deadline - checked
    ).total_seconds() <= cap


def previous_observation(
    record: dict[str, Any] | None,
    context: ValidationContext,
    policy: ValidationPolicy,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Do not inherit streaks from malformed, future or long-abandoned state."""
    previous = matching_record(record, context)
    checked = _parse_datetime(previous.get("checked_at")) if previous else None
    if (
        checked is None
        or previous is None
        or not _valid_observation(previous)
        or checked > now
        or (now - checked).total_seconds()
        >= 2 * max(policy.base_seconds, policy.max_success_seconds)
    ):
        return None
    return previous


def schedule_record(
    record: dict[str, Any],
    previous: dict[str, Any] | None,
    context: ValidationContext,
    policy: ValidationPolicy,
    *,
    now: datetime,
    active: bool | None,
) -> dict[str, Any]:
    """Attach the next deadline; cached observations must never call this."""
    previous = previous_observation(previous, context, policy, now=now)
    old = previous.get("consecutive_successes", 0) if previous else 0
    old = old if type(old) is int and old >= 0 else 0
    successes = min(old + 1, 4) if active is True else 0
    interval = policy.interval(
        active is True,
        successes if active is True else record["consecutive_failures"],
        transient=context.transient,
    )
    deadline = now + timedelta(seconds=interval)
    if context.expires_at is not None:
        deadline = min(deadline, context.expires_at)
    return {
        **record,
        "context_id": context.id,
        "schedule_version": POLICY_VERSION,
        "last_probe_active": active,
        "consecutive_successes": successes,
        "next_check_at": deadline.isoformat(),
    }
