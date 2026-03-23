from datetime import datetime, timezone

from legal_iptv.models import Channel, RunMetadata


def build_run_metadata(all_channels: list[Channel], selected: list[Channel]) -> RunMetadata:
    return RunMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_input_channels=len(all_channels),
        selected_channels=len(selected),
        extra_channels=sum(1 for item in all_channels if item.source == "extra"),
        iptv_org_channels=sum(1 for item in all_channels if item.source == "iptv_org"),
        live_stream_catalog_channels=sum(1 for item in all_channels if item.source == "live_stream_catalog"),
    )