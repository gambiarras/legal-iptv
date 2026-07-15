import logging
import time
from collections.abc import Callable

from legal_iptv.clients import HttpClient
from legal_iptv.config import AppConfig
from legal_iptv.exporters import render_m3u
from legal_iptv.io import write_json_atomic
from legal_iptv.models import Channel
from legal_iptv.services.channel_selector import select_best_channels
from legal_iptv.services.epg_mapper import enrich_epg_metadata, load_xmltv_aliases
from legal_iptv.services.link_validator import (
    filter_cached_offline_channels,
    refresh_stream_status,
)
from legal_iptv.services.metadata import build_run_metadata
from legal_iptv.sources import extra_channels, iptv_org, live_stream_catalog


logger = logging.getLogger(__name__)


def _fetch_source(
    source_name: str,
    fetcher: Callable[[], list[Channel]],
    source_errors: dict[str, str],
) -> list[Channel]:
    try:
        return fetcher()
    except Exception as exc:
        logger.exception("Failed to fetch source=%s error=%s", source_name, exc)
        source_errors[source_name] = str(exc)
        return []


def _timed(timings: dict[str, float], name: str, callback):
    started_at = time.perf_counter()
    try:
        return callback()
    finally:
        timings[name] = round(time.perf_counter() - started_at, 3)


def run_aggregation(config: AppConfig) -> None:
    logger.info("Starting aggregation")

    client = HttpClient()
    started_at = time.perf_counter()
    timings: dict[str, float] = {}

    try:
        source_errors: dict[str, str] = {}
        extra = _timed(timings, "fetch_extra", lambda: _fetch_source(
            "extra",
            extra_channels.fetch_channels,
            source_errors,
        ))
        iptv = _timed(timings, "fetch_iptv_org", lambda: _fetch_source(
            "iptv_org",
            lambda: iptv_org.fetch_channels(
                client,
                cache_file=config.iptv_org_cache_file,
                cache_ttl_seconds=config.iptv_org_cache_ttl_seconds,
                force_refresh=config.refresh_iptv_org_cache,
            ),
            source_errors,
        ))
        live = _timed(timings, "fetch_live_stream_catalog", lambda: _fetch_source(
            "live_stream_catalog",
            lambda: live_stream_catalog.fetch_channels(
                client,
                min_live_ttl=config.min_live_ttl,
                local_file=config.live_catalog_file,
            ),
            source_errors,
        ))

        all_channels = extra + iptv + live
        xmltv_aliases = _timed(timings, "load_epg_aliases", lambda: load_xmltv_aliases(
            client,
            cache_file=config.epg_cache_file,
            cache_ttl_seconds=config.epg_cache_ttl_seconds,
            force_refresh=config.refresh_epg_cache,
        ))
        enriched_channels = _timed(
            timings,
            "enrich_epg_metadata",
            lambda: enrich_epg_metadata(all_channels, xmltv_aliases=xmltv_aliases),
        )
        selected = _timed(timings, "select_channels", lambda: select_best_channels(enriched_channels))
        selected_before_stream_filter = len(selected)

        if config.validate_streams:
            selected = _timed(
                timings,
                "refresh_stream_status",
                lambda: refresh_stream_status(
                    selected,
                    status_file=config.stream_status_file,
                    max_workers=config.validation_max_workers,
                    timeout=config.validation_timeout,
                    max_age_seconds=config.stream_status_max_age,
                ),
            )
        else:
            selected = _timed(
                timings,
                "filter_cached_offline_channels",
                lambda: filter_cached_offline_channels(
                    selected,
                    status_file=config.stream_status_file,
                    max_age_seconds=config.stream_status_max_age,
                ),
            )

        playlist = _timed(timings, "render_m3u", lambda: render_m3u(selected))
        config.output_path.write_text(playlist, encoding="utf-8")
        timings["total"] = round(time.perf_counter() - started_at, 3)

        metadata = build_run_metadata(
            all_channels,
            selected,
            source_errors=source_errors,
            selected_before_stream_filter=selected_before_stream_filter,
            timings_seconds=timings,
            epg_aliases_count=len(xmltv_aliases),
        )
        write_json_atomic(config.meta_output_path, metadata.to_dict())

        logger.info(
            "Aggregation finished total_input=%s selected=%s source_errors=%s",
            len(all_channels),
            len(selected),
            len(source_errors),
        )

    finally:
        client.close()
