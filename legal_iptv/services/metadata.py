from datetime import datetime, timezone
from collections import Counter

from legal_iptv.models import Channel, RunMetadata


def _count_by_source(channels: list[Channel]) -> dict[str, int]:
    counts: dict[str, int] = {}

    for channel in channels:
        counts[channel.source] = counts.get(channel.source, 0) + 1

    return counts


def build_run_metadata(
    all_channels: list[Channel],
    selected: list[Channel],
    *,
    source_errors: dict[str, str] | None = None,
    selected_before_stream_filter: int | None = None,
    selected_after_stream_filter: int | None = None,
    timings_seconds: dict[str, float] | None = None,
    epg_aliases_count: int = 0,
    decision_counts: dict[str, int] | None = None,
    selection_decisions: list[dict[str, str]] | None = None,
    decisions_truncated: int = 0,
    catalog_input_sha256: str | None = None,
    ingestion_counts: dict[str, int] | None = None,
) -> RunMetadata:
    selected_before_filter = (
        len(selected)
        if selected_before_stream_filter is None
        else selected_before_stream_filter
    )
    selected_after_filter = (
        len(selected)
        if selected_after_stream_filter is None
        else selected_after_stream_filter
    )
    stream_filtered = max(0, selected_before_filter - selected_after_filter)
    selection_filtered = max(0, len(all_channels) - stream_filtered - len(selected))

    input_by_source = _count_by_source(all_channels)

    return RunMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_input_channels=len(all_channels),
        selected_channels=len(selected),
        extra_channels=input_by_source.get("extra", 0),
        public_catalog_channels=input_by_source.get("public_catalog", 0),
        live_stream_catalog_channels=input_by_source.get("live_stream_catalog", 0),
        source_errors=source_errors or {},
        decision_counts=decision_counts or {}, selection_decisions=selection_decisions or [],
        decisions_truncated=decisions_truncated, catalog_input_sha256=catalog_input_sha256,
        ingestion_counts=ingestion_counts or {},
        selected_before_stream_filter=selected_before_filter,
        selected_after_stream_filter=selected_after_filter,
        stream_filtered_channels=stream_filtered,
        selection_filtered_channels=selection_filtered,
        deduplicated_channels=(decision_counts.get("deduplicated", 0) if decision_counts is not None else selection_filtered),
        selected_by_source=_count_by_source(selected),
        selected_by_provider=dict(sorted(Counter(
            channel.provider_id or channel.source_type or channel.source for channel in selected
        ).items())),
        timings_seconds=timings_seconds or {},
        epg_aliases_count=epg_aliases_count,
    )
