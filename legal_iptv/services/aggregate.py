import logging

from legal_iptv.clients import HttpClient
from legal_iptv.config import AppConfig
from legal_iptv.exporters import render_m3u
from legal_iptv.io import write_json_atomic
from legal_iptv.services.channel_selector import select_best_channels
from legal_iptv.services.metadata import build_run_metadata
from legal_iptv.sources import extra_channels, iptv_org, live_stream_catalog


logger = logging.getLogger(__name__)


def run_aggregation(config: AppConfig) -> None:
    logger.info("Starting aggregation")

    client = HttpClient()

    try:
        extra = extra_channels.fetch_channels()
        iptv = iptv_org.fetch_channels(client)
        live = live_stream_catalog.fetch_channels(client, min_live_ttl=config.min_live_ttl)

        all_channels = extra + iptv + live
        selected = select_best_channels(all_channels)

        playlist = render_m3u(selected)
        config.output_path.write_text(playlist, encoding="utf-8")

        metadata = build_run_metadata(all_channels, selected)
        write_json_atomic(config.meta_output_path, metadata.to_dict())

        logger.info(
            "Aggregation finished total_input=%s selected=%s",
            len(all_channels),
            len(selected),
        )

    finally:
        client.close()