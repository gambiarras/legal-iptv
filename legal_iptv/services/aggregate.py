import logging
import json
import re
import time
from collections.abc import Callable

from legal_iptv.clients import HttpClient
from legal_iptv.config import AppConfig
from legal_iptv.local_config import require_local_configuration
from legal_iptv.exporters import render_m3u
from legal_iptv.io import write_json_atomic, write_text_atomic
from legal_iptv.models import Channel
from legal_iptv.services.epg_mapper import (
    load_local_xmltv_aliases,
    load_xmltv_aliases,
)
from legal_iptv.services.guide import ensure_guide, project_guide, load_guide_inventory_aliases
from legal_iptv.services.companion import enrich_companion_candidates, run_companion, _transport_safe
from legal_iptv.services.companion_reference import load_reference, validate_reference
from legal_iptv.services.channel_identity import canonicalize_channels, rejection_reason, load_policy
from legal_iptv.services.link_validator import (
    filter_cached_offline_channels,
    filter_media_confirmed_channels,
    refresh_stream_status,
)
from legal_iptv.services.metadata import build_run_metadata
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.services.validation_schedule import ValidationPolicy
from legal_iptv.sources import extra_channels, public_catalog, live_stream_catalog

logger = logging.getLogger(__name__)
PRIVATE_ERROR_PATTERN = re.compile(
    r"https?://|(?:token|cookie|authorization|bearer|signature)=?",
    re.IGNORECASE,
)


def _public_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if not message or PRIVATE_ERROR_PATTERN.search(message):
        return type(exc).__name__
    return message[:200]


def _fetch_source(
    source_name: str,
    fetcher: Callable[[], list[Channel]],
    source_errors: dict[str, str],
) -> list[Channel]:
    try:
        return fetcher()
    except Exception as exc:
        logger.error(
            "Failed to fetch source=%s error_type=%s",
            source_name,
            type(exc).__name__,
        )
        source_errors[source_name] = _public_error(exc)
        return []


def _timed(timings: dict[str, float], name: str, callback):
    started_at = time.perf_counter()
    try:
        return callback()
    finally:
        timings[name] = round(time.perf_counter() - started_at, 3)


def _required_ids(channels: list[Channel]) -> tuple[str, ...]:
    selected = {channel.tvg_id for channel in channels}
    return tuple(value for value in load_policy().get("required_channel_ids", []) if value in selected)


def run_aggregation(config: AppConfig) -> None:
    require_local_configuration()
    logger.info("Starting aggregation")

    client = HttpClient()
    started_at = time.perf_counter()
    timings: dict[str, float] = {}

    try:
        source_errors: dict[str, str] = {}
        playlist_configuration = load_playlist_configuration(config.profile_config_file)
        extra = _timed(timings, "fetch_extra", lambda: _fetch_source(
            "extra",
            lambda: extra_channels.fetch_channels(
                config.extra_removed_file, include_removed=config.validate_streams),
            source_errors,
        ))
        iptv = _timed(timings, "fetch_public_catalog", lambda: _fetch_source(
            "public_catalog",
            lambda: public_catalog.fetch_channels(
                client,
                cache_file=config.public_catalog_cache_file,
                cache_ttl_seconds=config.public_catalog_cache_ttl_seconds,
                force_refresh=config.refresh_public_catalog_cache,
                cache_only=config.validation_due_only,
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
        if config.validation_due_only and source_errors:
            raise RuntimeError("Due-only validation requires intact local source caches")
        reference = None
        if config.companion_output_path:
            reference_cache = (config.companion_reference_cache_path or
                               config.epg_cache_file.parent / ".cache" / "companion-reference.json")
            if config.validation_due_only:
                reference = validate_reference(json.loads(
                    (config.companion_reference_file or reference_cache).read_text()))
            else:
                reference = load_reference(client, reference_cache,
                                           local_file=config.companion_reference_file)

        def load_epg_aliases():
            aliases = {}
            if config.guide_output_path.exists():
                try:
                    aliases.update(load_local_xmltv_aliases(config.guide_output_path))
                except Exception as exc:
                    logger.warning(
                        "Local guide cannot be used for aliases error_type=%s",
                        type(exc).__name__,
                    )
            fallback_aliases = load_xmltv_aliases(
                client,
                cache_file=config.epg_cache_file,
                cache_ttl_seconds=config.epg_cache_ttl_seconds,
                force_refresh=config.refresh_epg_cache,
                cache_only=(config.validation_due_only or config.ensure_guide_enabled
                            or config.guide_output_path.exists()),
            )
            inventory = load_guide_inventory_aliases(config.guide_output_path.parent / ".epg-cache")
            for key, value in inventory.items():
                aliases.setdefault(key, value)
            for key, value in fallback_aliases.items():
                aliases.setdefault(key, value)
            return aliases

        xmltv_aliases = _timed(timings, "load_epg_aliases", load_epg_aliases)
        enriched_channels = _timed(
            timings,
            "enrich_epg_metadata",
            lambda: canonicalize_channels(enrich_companion_candidates(
                [channel for channel in all_channels
                 if not rejection_reason(channel) and _transport_safe(channel)], xmltv_aliases)),
        )
        if config.profile == "base":
            selected = _timed(
                timings,
                "select_channels",
                lambda: select_profile_channels(
                    enriched_channels,
                    playlist_configuration,
                    "base",
                    player_profile=config.player_profile,
                ),
            )
        else:
            selected = enriched_channels

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
                    failure_threshold=config.validation_failure_threshold,
                    extra_removed_file=config.extra_removed_file,
                    policy=ValidationPolicy(
                        base_seconds=config.stream_status_max_age,
                        max_success_seconds=config.validation_max_success_age,
                        retry_seconds=config.validation_retry_delay,
                    ),
                    force=config.force_validation,
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

        if config.require_media_proof:
            selected = _timed(timings, "filter_media_proof", lambda: filter_media_confirmed_channels(
                selected, status_file=config.stream_status_file,
                max_age_seconds=config.validation_max_success_age,
            ))
        selected_after_stream_filter = len(selected)
        if config.profile != "base":
            selected = _timed(
                timings,
                "select_channels",
                lambda: select_profile_channels(
                    selected,
                    playlist_configuration,
                    config.profile,
                    player_profile=config.player_profile,
                ),
            )

        if config.ensure_guide_enabled:
            # Normalize the final winners before keying guide caches. Doing this
            # only after collection made subset mappings alternate each run.
            selected = canonicalize_channels(enrich_companion_candidates(selected, xmltv_aliases))
            _timed(timings, "ensure_guide", lambda: ensure_guide(
                client, output_path=config.guide_output_path,
                diagnostics_path=config.guide_diagnostics_file,
                ttl_seconds=config.guide_ttl_seconds,
                lkg_seconds=config.guide_lkg_seconds,
                min_coverage_ratio=config.guide_min_coverage_ratio,
                selected_channel_ids={channel.tvg_id for channel in selected if channel.tvg_id},
                selected_channel_names={channel.tvg_id: channel.name for channel in selected if channel.tvg_id},
                required_channel_ids=_required_ids(selected),
            ))
            # Upstream inventory adds aliases without retaining a world-sized XMLTV.
            inventory = load_guide_inventory_aliases(config.guide_output_path.parent / ".epg-cache")
            if inventory:
                updated_aliases = {**inventory, **xmltv_aliases}
                remapped = canonicalize_channels(enrich_companion_candidates(selected, updated_aliases))
                if {item.tvg_id for item in remapped} != {item.tvg_id for item in selected}:
                    _timed(timings, "remap_guide", lambda: ensure_guide(
                        client, output_path=config.guide_output_path,
                        diagnostics_path=config.guide_diagnostics_file,
                        ttl_seconds=config.guide_ttl_seconds,
                        lkg_seconds=config.guide_lkg_seconds,
                        min_coverage_ratio=config.guide_min_coverage_ratio,
                        selected_channel_ids={item.tvg_id for item in remapped if item.tvg_id},
                        selected_channel_names={item.tvg_id: item.name for item in remapped if item.tvg_id},
                        required_channel_ids=_required_ids(remapped),
                    ))
                selected = remapped
            _timed(timings, "project_guide", lambda: project_guide(
                config.guide_output_path, config.guide_output_path,
                {channel.tvg_id for channel in selected if channel.tvg_id},
                channel_names={channel.tvg_id: channel.name for channel in selected if channel.tvg_id},
                required_channel_ids=_required_ids(selected),
            ))

        guide_url = config.guide_url or playlist_configuration.guide_url
        playlist = _timed(
            timings,
            "render_m3u",
            lambda: render_m3u(
                selected,
                guide_url=guide_url,
                player_profile=config.player_profile,
                category_order=(
                    None
                    if config.profile == "base"
                    else playlist_configuration.group_order
                ),
                legacy_guide_urls=True,
                enforce_capabilities=config.profile != "base",
                alternatives_group=playlist_configuration.alternatives_group,
            ),
        )
        if config.companion_output_path:
            _timed(timings, "generate_companion", lambda: run_companion(
                selected, config, client, reference=reference,
            ))
        write_text_atomic(config.output_path, playlist)
        timings["total"] = round(time.perf_counter() - started_at, 3)

        metadata = build_run_metadata(
            all_channels,
            selected,
            source_errors=source_errors,
            selected_before_stream_filter=selected_before_stream_filter,
            selected_after_stream_filter=selected_after_stream_filter,
            timings_seconds=timings,
            epg_aliases_count=len(xmltv_aliases),
        )
        payload = metadata.to_dict()
        payload["media_proof_required"] = config.require_media_proof
        write_json_atomic(config.meta_output_path, payload)

        logger.info(
            "Aggregation finished total_input=%s selected=%s source_errors=%s",
            len(all_channels),
            len(selected),
            len(source_errors),
        )

    finally:
        client.close()
