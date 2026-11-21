"""Bounded observation history, independent of HTTP execution and suppression."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from legal_iptv.models.stream_validation import UrlValidation
from legal_iptv.services.stream_status import _parse_datetime

KINDS = ("transports", "channels", "providers")
COUNTERS = (
    "checks",
    "successful_checks",
    "offline_checks",
    "unknown_checks",
    "http_request_attempts",
    "request_measured_checks",
    "timed_checks",
)
IDENTITY_FIELDS = (
    "provider_id",
    "logical_channel_id",
    "feed_id",
    "channel_id",
    "name",
    "source",
    "source_type",
    "transport_id",
)
PRIVATE_TEXT = re.compile(
    r"://|(?:token|signature|authorization|cookie|password|api[_-]?key|credential|secret)\s*[:=]|\bbearer\s+\S+",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class ValidationMetrics:
    history: dict[str, Any]
    summary: dict[str, Any]
    top_failing_channels: list[dict[str, Any]]
    providers: list[dict[str, Any]]


def transport_id(url: str) -> str:
    """Identify the exact URL without exposing it in history or rankings."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _key(values: list[object]) -> str:
    return transport_id(json.dumps(values, ensure_ascii=False, separators=(",", ":")))


def safe_metric_label(value: object) -> str:
    """Keep free URLs or credential-shaped text out of aggregate labels."""
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (str, int))
    ):
        return "[invalid]"
    text = " ".join(str(value or "").split())
    return "[redacted]" if PRIVATE_TEXT.search(text) else text[:200]


def _error_type(value: object) -> str | None:
    if value is None:
        return None
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value)
        else "OtherError"
    )


def _count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        duration = float(value)
    except (OverflowError, ValueError):
        return None
    return duration if math.isfinite(duration) and duration >= 0 else None


def _empty_counts() -> dict[str, Any]:
    return {**dict.fromkeys(COUNTERS, 0), "total_duration_ms": 0.0}


def _event(result: UrlValidation, checked_at: str) -> dict[str, Any]:
    outcome = (
        "success"
        if result.active is True
        else "offline" if result.active is False else "unknown"
    )
    event: dict[str, Any] = {"checked_at": checked_at, "outcome": outcome}
    if type(result.http_status) is int and 100 <= result.http_status <= 599:
        event["http_status"] = result.http_status
    if result.error_type is not None:
        event["error_type"] = _error_type(result.error_type)
    duration = _duration(result.duration_ms)
    if duration is not None:
        event["duration_ms"] = duration
    if type(result.request_count) is int and result.request_count >= 0:
        event["request_count"] = result.request_count
    return event


def _observe(counts: dict[str, Any], event: dict[str, Any]) -> None:
    counts["checks"] += 1
    field = {
        "success": "successful_checks",
        "offline": "offline_checks",
        "unknown": "unknown_checks",
    }[event["outcome"]]
    counts[field] += 1
    if "duration_ms" in event:
        counts["timed_checks"] += 1
        counts["total_duration_ms"] = round(
            counts["total_duration_ms"] + event["duration_ms"], 3
        )
    if "request_count" in event:
        counts["request_measured_checks"] += 1
        counts["http_request_attempts"] += event["request_count"]


def _update_series(
    collection: dict[str, dict[str, Any]],
    key: str,
    identity: dict[str, Any],
    event: dict[str, Any],
    max_events: int,
) -> None:
    series = collection.setdefault(
        key,
        {
            **_empty_counts(),
            "identity": {},
            "first_checked_at": event["checked_at"],
            "consecutive_successes": 0,
            "consecutive_failures": 0,
            "http_status_counts": {},
            "error_type_counts": {},
            "recent_events": [],
        },
    )
    series["identity"] = {
        k: safe_metric_label(v) for k, v in identity.items() if k in IDENTITY_FIELDS
    }
    _observe(series, event)
    success = event["outcome"] == "success"
    series["consecutive_successes"] = (
        series["consecutive_successes"] + 1 if success else 0
    )
    series["consecutive_failures"] = (
        0 if success else series["consecutive_failures"] + 1
    )
    series["last_checked_at"] = event["checked_at"]
    series["last_outcome"] = event["outcome"]
    series["last_success_at" if success else "last_failure_at"] = event["checked_at"]
    for event_field, counts_field in (
        ("http_status", "http_status_counts"),
        ("error_type", "error_type_counts"),
    ):
        if event_field in event:
            label = str(event[event_field])
            series[counts_field][label] = series[counts_field].get(label, 0) + 1
    series["recent_events"] = (series["recent_events"] + [dict(event)])[-max_events:]


def _restore_series(
    raw: object, cutoff: datetime, now: datetime, max_events: int
) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("identity"), dict):
        return None
    first = _parse_datetime(raw.get("first_checked_at"))
    last = _parse_datetime(raw.get("last_checked_at"))
    if first is None or last is None or not first <= last or not cutoff <= last <= now:
        return None
    if any(type(raw.get(k)) is not int or raw[k] < 0 for k in COUNTERS):
        return None
    if (
        raw["checks"]
        != raw["successful_checks"] + raw["offline_checks"] + raw["unknown_checks"]
    ):
        return None
    if (
        not raw["checks"]
        or raw["timed_checks"] > raw["checks"]
        or raw["request_measured_checks"] > raw["checks"]
    ):
        return None
    total_duration = _duration(raw.get("total_duration_ms"))
    if total_duration is None:
        return None
    series = {
        **{k: raw[k] for k in COUNTERS},
        "total_duration_ms": total_duration,
        "identity": {
            k: safe_metric_label(v)
            for k, v in raw["identity"].items()
            if k in IDENTITY_FIELDS
        },
        "first_checked_at": first.astimezone(timezone.utc).isoformat(),
        "last_checked_at": last.astimezone(timezone.utc).isoformat(),
        "last_outcome": (
            raw.get("last_outcome")
            if raw.get("last_outcome") in ("success", "offline", "unknown")
            else "unknown"
        ),
        "consecutive_successes": min(
            _count(raw.get("consecutive_successes")), raw["checks"]
        ),
        "consecutive_failures": min(
            _count(raw.get("consecutive_failures")), raw["checks"]
        ),
        "http_status_counts": {},
        "error_type_counts": {},
        "recent_events": [],
    }
    for field in ("last_success_at", "last_failure_at"):
        timestamp = _parse_datetime(raw.get(field))
        if timestamp is not None and first <= timestamp <= last:
            series[field] = timestamp.astimezone(timezone.utc).isoformat()
    for field in ("http_status_counts", "error_type_counts"):
        labels = raw.get(field)
        if isinstance(labels, dict):
            for label, count in labels.items():
                safe = (
                    label
                    if field == "http_status_counts"
                    and isinstance(label, str)
                    and re.fullmatch(r"[1-5][0-9]{2}", label)
                    else _error_type(label) if field == "error_type_counts" else None
                )
                if safe and _count(count):
                    series[field][safe] = series[field].get(safe, 0) + min(
                        count, raw["checks"]
                    )
    events = raw.get("recent_events")
    if isinstance(events, list):
        for event in events[-max_events:]:
            if not isinstance(event, dict):
                continue
            timestamp = _parse_datetime(event.get("checked_at"))
            if timestamp is None or not cutoff <= timestamp <= last:
                continue
            active = {"success": True, "offline": False, "unknown": None}
            if isinstance(event.get("outcome"), str) and event["outcome"] in active:
                result = UrlValidation(
                    active[event["outcome"]],
                    event.get("http_status"),
                    event.get("error_type"),
                    event.get("duration_ms"),
                    event.get("request_count"),
                )
                series["recent_events"].append(
                    _event(result, timestamp.astimezone(timezone.utc).isoformat())
                )
    return series


def _summary(series: dict[str, Any]) -> dict[str, Any]:
    checks = series["checks"]
    failed = series["offline_checks"] + series["unknown_checks"]
    return {
        **series["identity"],
        **{k: series[k] for k in COUNTERS},
        "first_checked_at": series["first_checked_at"],
        "last_checked_at": series["last_checked_at"],
        "unsuccessful_checks": failed,
        "unsuccessful_rate": round(failed / checks, 4),
        "confirmed_offline_rate": round(series["offline_checks"] / checks, 4),
        "mean_duration_ms": (
            round(series["total_duration_ms"] / series["timed_checks"], 3)
            if series["timed_checks"]
            else None
        ),
    }


def build_validation_metrics(
    previous_snapshot: dict[str, Any],
    results: dict[str, UrlValidation],
    metadata_by_url: dict[str, dict[str, Any]],
    *,
    now: datetime,
    cached_urls: int,
    suppressed_urls: int,
    retention_days: int = 30,
    max_series: int = 5000,
    max_events: int = 20,
) -> ValidationMetrics:
    """Count actual URL observations; never count cached results as new evidence.

    Counters cover the retained series lifetime, not uptime or a time window.
    Inactive series expire; recent events have both age and count limits.
    """
    if min(retention_days, max_series, max_events) < 1:
        raise ValueError("History limits must be positive")
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    checked_at = now.isoformat()
    old_history = previous_snapshot.get("history")
    old_history = old_history if isinstance(old_history, dict) else {}
    collections: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in KINDS}
    for kind in KINDS:
        previous = old_history.get(kind)
        if isinstance(previous, dict):
            for key, raw in previous.items():
                if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
                    continue
                restored = _restore_series(raw, cutoff, now, max_events)
                if restored is not None:
                    collections[kind][key] = restored

    totals = _empty_counts()
    http_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    for url in sorted(results):
        result = results[url]
        event = _event(result, checked_at)
        _observe(totals, event)
        for field, counts in (
            ("http_status", http_counts),
            ("error_type", error_counts),
        ):
            if field in event:
                label = str(event[field])
                counts[label] = counts.get(label, 0) + 1
        tid = transport_id(url)
        _update_series(
            collections["transports"], tid, {"transport_id": tid}, event, max_events
        )
        attributed: set[tuple[str, str]] = set()
        for channel in metadata_by_url.get(url, {}).get("channels", []):
            provider = (
                channel.get("provider_id")
                or channel.get("source_type")
                or channel.get("source")
                or "unknown"
            )
            logical = channel.get("logical_channel_id") or channel.get("id")
            feed = channel.get("feed_id")
            identity = {
                "provider_id": provider,
                "logical_channel_id": logical,
                "feed_id": feed,
                "channel_id": channel.get("id"),
                "name": channel.get("name"),
                "source": channel.get("source"),
                "source_type": channel.get("source_type"),
            }
            targets = (
                ("channels", _key([provider, logical, feed]), identity),
                ("providers", _key([provider]), {"provider_id": provider}),
            )
            for kind, key, identity in targets:
                if (kind, key) not in attributed:
                    _update_series(collections[kind], key, identity, event, max_events)
                    attributed.add((kind, key))

    for kind, collection in collections.items():
        ordered = sorted(
            collection,
            key=lambda key: (collection[key]["last_checked_at"], key),
            reverse=True,
        )
        collections[kind] = {key: collection[key] for key in ordered[:max_series]}
    channel_summaries = [
        _summary(series) for series in collections["channels"].values()
    ]
    failures = [item for item in channel_summaries if item["unsuccessful_checks"]]
    failures.sort(
        key=lambda item: (
            -item["unsuccessful_checks"],
            -item["unsuccessful_rate"],
            item.get("provider_id", ""),
            item.get("logical_channel_id", ""),
        )
    )
    providers = [_summary(series) for series in collections["providers"].values()]
    providers.sort(key=lambda item: item.get("provider_id", ""))
    return ValidationMetrics(
        history={
            "counter_scope": "retained_series_lifetime",
            "validation_level": "http",
            "retention_days": retention_days,
            "max_series_per_kind": max_series,
            "max_events_per_series": max_events,
            **collections,
        },
        summary={
            **totals,
            "validation_level": "http",
            "cached_urls": cached_urls,
            "cache_hit_rate": (
                round(cached_urls / (len(results) + cached_urls), 4)
                if results or cached_urls
                else 0.0
            ),
            "suppressed_urls": suppressed_urls,
            "http_status_counts": http_counts,
            "error_type_counts": error_counts,
            "mean_duration_ms": (
                round(totals["total_duration_ms"] / totals["timed_checks"], 3)
                if totals["timed_checks"]
                else None
            ),
        },
        top_failing_channels=failures[:20],
        providers=providers,
    )
