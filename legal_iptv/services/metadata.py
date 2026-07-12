from datetime import datetime, timezone

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
) -> RunMetadata:
    selected_before_filter = (
        len(selected)
        if selected_before_stream_filter is None
        else selected_before_stream_filter
    )

    return RunMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_input_channels=len(all_channels),
        selected_channels=len(selected),
        extra_channels=sum(1 for item in all_channels if item.source == "extra"),
        iptv_org_channels=sum(1 for item in all_channels if item.source == "iptv_org"),
        live_stream_catalog_channels=sum(1 for item in all_channels if item.source == "live_stream_catalog"),
        source_errors=source_errors or {},
        selected_before_stream_filter=selected_before_filter,
        stream_filtered_channels=max(0, selected_before_filter - len(selected)),
        deduplicated_channels=max(0, len(all_channels) - selected_before_filter),
        selected_by_source=_count_by_source(selected),
    )
