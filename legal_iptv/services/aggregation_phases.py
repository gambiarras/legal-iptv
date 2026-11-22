"""Typed phase results and bounded, transport-free selection explanations."""

import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from legal_iptv.models import Channel


def _safe(value: object) -> str:
    text = str(value or "")
    if len(text) > 160 or re.search(
        r"://|[\r\n]|(?:token|cookie|password|authorization|signature)\s*[=:]",
        text,
        re.IGNORECASE,
    ):
        return "redacted"
    return text


def candidate_key(channel: Channel) -> tuple[Any, ...]:
    """Keep internal transport context for matching, never for serialization."""
    return (
        channel.source,
        channel.provider_id,
        channel.feed_id,
        channel.logical_channel_id,
        channel.stream_url,
        tuple(sorted(channel.request_headers.items())),
        channel.resolution,
        channel.codec,
        channel.bitrate,
        channel.variant_label,
    )


@dataclass(slots=True)
class DecisionLedger:
    limit: int = 200
    counts: Counter[str] = field(default_factory=Counter)
    decisions: list[dict[str, str]] = field(default_factory=list)
    truncated: int = 0

    def record(self, channel: Channel, reason: str, phase: str) -> None:
        self.counts[reason] += 1
        if len(self.decisions) >= self.limit:
            self.truncated += 1
            return
        self.decisions.append(
            {
                "id": _safe(channel.id),
                "logical_id": _safe(channel.logical_channel_id),
                "provider": _safe(channel.provider_id or channel.source),
                "group": _safe(channel.group),
                "reason": reason,
                "phase": phase,
                "category_origin": _safe(channel.category_origin),
                "category_reason": _safe(channel.category_reason),
                "category_rule": _safe(channel.category_rule),
            }
        )

    def removed(
        self,
        before: list[Channel],
        after: list[Channel],
        reason: str | Callable[[Channel], str],
        phase: str,
    ) -> None:
        retained = Counter(candidate_key(channel) for channel in after)
        unmatched = []
        for channel in before:
            key = candidate_key(channel)
            if retained[key]:
                retained[key] -= 1
            else:
                unmatched.append(channel)
        # An explicit logical-ID override changes presentation, not retention.
        # Reserve exact identities first so equal transports cannot steal them.
        transformed: Counter[tuple[Any, ...]] = Counter()
        for key, count in retained.items():
            transformed[key[:3] + key[4:]] += count
        for channel in unmatched:
            key = candidate_key(channel)
            transport = key[:3] + key[4:]
            if transformed[transport]:
                transformed[transport] -= 1
            else:
                self.record(
                    channel, reason(channel) if callable(reason) else reason, phase
                )


@dataclass(frozen=True, slots=True)
class CollectionResult:
    channels: list[Channel]
    source_errors: Mapping[str, str]
    catalog_input_sha256: str | None
    ingestion_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    channels: list[Channel]
    input_count: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    channels: list[Channel]
    input_count: int
    unavailable_count: int
    unconfirmed_count: int


@dataclass(frozen=True, slots=True)
class SelectionResult:
    channels: list[Channel]
    input_count: int


@dataclass(frozen=True, slots=True)
class GuidePhaseResult:
    channels: list[Channel]
    guide_path: Path
    enabled: bool


@dataclass(frozen=True, slots=True)
class PublicationResult:
    selected_count: int
    output_path: Path
    metadata_path: Path


def collect_sources(
    fetchers: Mapping[str, Callable[[], list[Channel]]],
    errors: Mapping[str, str],
    provenance: dict[str, Any],
) -> CollectionResult:
    channels = [channel for fetcher in fetchers.values() for channel in fetcher()]
    return CollectionResult(
        channels,
        dict(errors),
        provenance.get("catalog_input_sha256"),
        dict(provenance.get("ingestion_counts", {})),
    )


def prepare_candidates(
    channels: list[Channel],
    reason: Callable[[Channel], str | None],
    transform: Callable[[list[Channel]], list[Channel]],
    ledger: DecisionLedger,
) -> EligibilityResult:
    eligible = []
    for channel in channels:
        rejected = reason(channel)
        if rejected:
            ledger.record(channel, rejected, "eligibility")
        else:
            eligible.append(channel)
    return EligibilityResult(transform(eligible), len(channels))


def validate_candidates(
    channels: list[Channel],
    validator: Callable[[list[Channel]], list[Channel]],
    proof: Callable[[list[Channel]], list[Channel]],
    ledger: DecisionLedger,
) -> ValidationResult:
    available = validator(channels)
    ledger.removed(channels, available, "unavailable", "validation")
    confirmed = proof(available)
    ledger.removed(available, confirmed, "media_unconfirmed", "proof")
    return ValidationResult(
        confirmed,
        len(channels),
        len(channels) - len(available),
        len(available) - len(confirmed),
    )


def select_candidates(
    channels: list[Channel],
    selector: Callable[[list[Channel]], list[Channel]],
    reason: Callable[[Channel], str],
    ledger: DecisionLedger,
) -> SelectionResult:
    selected = selector(channels)
    ledger.removed(channels, selected, reason, "selection")
    return SelectionResult(selected, len(channels))
