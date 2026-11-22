import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from legal_iptv.clients import HttpClient
from legal_iptv.config import AppConfig
from legal_iptv.exporters import render_m3u
from legal_iptv.io import write_json_atomic, write_text_atomic
from legal_iptv.local_config import configuration_snapshot, require_local_configuration
from legal_iptv.models import Channel
from legal_iptv.services.aggregation_phases import (
    CollectionResult,
    DecisionLedger,
    GuidePhaseResult,
    PublicationResult,
    collect_sources,
    prepare_candidates,
    select_candidates,
    validate_candidates,
)
from legal_iptv.services.channel_identity import (
    canonicalize_channels,
    load_policy,
    rejection_reason,
)
from legal_iptv.services.companion import (
    _transport_safe,
    enrich_companion_candidates,
    run_companion,
)
from legal_iptv.services.companion_reference import load_reference, validate_reference
from legal_iptv.services.epg_mapper import (
    load_local_xmltv_aliases,
    load_xmltv_aliases,
)
from legal_iptv.services.guide import (
    ensure_guide,
    load_guide_inventory_aliases,
    project_guide,
)
from legal_iptv.services.link_validator import (
    filter_cached_offline_channels,
    filter_media_confirmed_channels,
    refresh_stream_status,
)
from legal_iptv.services.metadata import build_run_metadata
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import (
    _allowed_by_profile,
    _base_candidates,
    select_profile_channels,
)
from legal_iptv.services.publication import is_exportable
from legal_iptv.services.selection_proof import write_selection_proof
from legal_iptv.services.validation_schedule import ValidationPolicy
from legal_iptv.services.video_platform_transport import is_public_video_platform_hls
from legal_iptv.sources import extra_channels, live_stream_catalog, public_catalog

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
    return tuple(
        value
        for value in load_policy().get("required_channel_ids", [])
        if value in selected
    )


def _collect_phase(
    config: AppConfig,
    client: HttpClient,
    timings: dict[str, float],
    source_errors: dict[str, str],
) -> CollectionResult:
    provenance: dict = {}
    fetchers = {
        "extra": lambda: _timed(
            timings,
            "fetch_extra",
            lambda: _fetch_source(
                "extra",
                lambda: extra_channels.fetch_channels(
                    config.extra_removed_file, include_removed=config.validate_streams
                ),
                source_errors,
            ),
        ),
        "iptv": lambda: _timed(
            timings,
            "fetch_public_catalog",
            lambda: _fetch_source(
                "public_catalog",
                lambda: public_catalog.fetch_channels(
                    client,
                    cache_file=config.public_catalog_cache_file,
                    cache_ttl_seconds=config.public_catalog_cache_ttl_seconds,
                    force_refresh=config.refresh_public_catalog_cache,
                    cache_only=config.validation_due_only,
                ),
                source_errors,
            ),
        ),
        "live": lambda: _timed(
            timings,
            "fetch_live_stream_catalog",
            lambda: _fetch_source(
                "live_stream_catalog",
                lambda: live_stream_catalog.fetch_channels(
                    client,
                    min_live_ttl=config.min_live_ttl,
                    local_file=config.live_catalog_file,
                    provenance=provenance,
                ),
                source_errors,
            ),
        ),
    }
    return collect_sources(fetchers, source_errors, provenance)


def _load_alias_phase(config: AppConfig, client: HttpClient) -> dict:
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
        cache_only=(
            config.validation_due_only
            or config.ensure_guide_enabled
            or config.guide_output_path.exists()
        ),
    )
    inventory = load_guide_inventory_aliases(
        config.guide_output_path.parent / ".epg-cache"
    )
    for key, value in inventory.items():
        aliases.setdefault(key, value)
    for key, value in fallback_aliases.items():
        aliases.setdefault(key, value)
    return aliases


def _validate_phase(
    config: AppConfig, selected: list[Channel], timings: dict[str, float]
) -> list[Channel]:
    if config.validate_streams:
        return _timed(
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
        return _timed(
            timings,
            "filter_cached_offline_channels",
            lambda: filter_cached_offline_channels(
                selected,
                status_file=config.stream_status_file,
                max_age_seconds=config.stream_status_max_age,
            ),
        )


def _guide_phase(
    config: AppConfig,
    client: HttpClient,
    selected: list[Channel],
    xmltv_aliases: dict,
    timings: dict[str, float],
    ledger: DecisionLedger,
) -> GuidePhaseResult:
    if config.ensure_guide_enabled:
        # Normalize the final winners before keying guide caches. Doing this
        # only after collection made subset mappings alternate each run.
        selected = canonicalize_channels(
            enrich_companion_candidates(selected, xmltv_aliases)
        )
        _timed(
            timings,
            "ensure_guide",
            lambda: ensure_guide(
                client,
                output_path=config.guide_output_path,
                diagnostics_path=config.guide_diagnostics_file,
                ttl_seconds=config.guide_ttl_seconds,
                lkg_seconds=config.guide_lkg_seconds,
                min_coverage_ratio=config.guide_min_coverage_ratio,
                selected_channel_ids={
                    channel.tvg_id for channel in selected if channel.tvg_id
                },
                selected_channel_names={
                    channel.tvg_id: channel.name
                    for channel in selected
                    if channel.tvg_id
                },
                required_channel_ids=_required_ids(selected),
            ),
        )
        # Upstream inventory adds aliases without retaining a world-sized XMLTV.
        inventory = load_guide_inventory_aliases(
            config.guide_output_path.parent / ".epg-cache"
        )
        if inventory:
            updated_aliases = {**inventory, **xmltv_aliases}
            remapped = canonicalize_channels(
                enrich_companion_candidates(selected, updated_aliases)
            )
            if {item.tvg_id for item in remapped} != {item.tvg_id for item in selected}:
                _timed(
                    timings,
                    "remap_guide",
                    lambda: ensure_guide(
                        client,
                        output_path=config.guide_output_path,
                        diagnostics_path=config.guide_diagnostics_file,
                        ttl_seconds=config.guide_ttl_seconds,
                        lkg_seconds=config.guide_lkg_seconds,
                        min_coverage_ratio=config.guide_min_coverage_ratio,
                        selected_channel_ids={
                            item.tvg_id for item in remapped if item.tvg_id
                        },
                        selected_channel_names={
                            item.tvg_id: item.name for item in remapped if item.tvg_id
                        },
                        required_channel_ids=_required_ids(remapped),
                    ),
                )
            selected = remapped
    # Collection can outlast a renewable URL: keep guide, metadata and lists aligned.
    previous = selected
    selected = [
        channel
        for channel in selected
        if (
            channel.source_type != "video_platform"
            or is_public_video_platform_hls(channel)
        )
    ]
    ledger.removed(previous, selected, "renewable_ttl", "freshness")
    if config.ensure_guide_enabled or config.guide_output_path.is_file():
        _timed(
            timings,
            "project_guide",
            lambda: project_guide(
                config.guide_output_path,
                config.guide_output_path,
                {channel.tvg_id for channel in selected if channel.tvg_id},
                channel_names={
                    channel.tvg_id: channel.name
                    for channel in selected
                    if channel.tvg_id
                },
                required_channel_ids=_required_ids(selected),
                diagnostics_path=config.guide_diagnostics_file,
                require_coverage=config.ensure_guide_enabled,
            ),
        )

    return GuidePhaseResult(
        selected, config.guide_output_path, config.ensure_guide_enabled
    )


def _publish_phase(
    config: AppConfig,
    client: HttpClient,
    selected: list[Channel],
    collection: CollectionResult,
    playlist_configuration,
    xmltv_aliases: dict,
    source_errors: dict[str, str],
    timings: dict[str, float],
    started_at: float,
    selected_before_stream_filter: int,
    selected_after_stream_filter: int,
    ledger: DecisionLedger,
    reference: dict | None,
) -> PublicationResult:
    all_channels = collection.channels
    if config.selection_proof_output_path is not None:
        write_selection_proof(
            selected,
            config.selection_proof_output_path,
            config.stream_status_file,
            max_age_seconds=min(config.validation_max_success_age, 86400),
            min_ttl_seconds=config.min_live_ttl,
        )
    guide_url = config.guide_url or playlist_configuration.guide_url
    playlist = _timed(
        timings,
        "render_m3u",
        lambda: render_m3u(
            selected,
            guide_url=guide_url,
            player_profile=config.player_profile,
            category_order=(
                None if config.profile == "base" else playlist_configuration.group_order
            ),
            legacy_guide_urls=True,
            enforce_capabilities=config.profile != "base",
            alternatives_group=playlist_configuration.alternatives_group,
        ),
    )
    if config.companion_output_path:
        _timed(
            timings,
            "generate_companion",
            lambda: run_companion(
                selected,
                config,
                client,
                reference=reference,
            ),
        )
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
        decision_counts=dict(ledger.counts),
        selection_decisions=ledger.decisions,
        decisions_truncated=ledger.truncated,
        catalog_input_sha256=collection.catalog_input_sha256,
        ingestion_counts=dict(collection.ingestion_counts),
    )
    payload = metadata.to_dict()
    payload["media_proof_required"] = config.require_media_proof
    write_json_atomic(config.meta_output_path, payload)

    return PublicationResult(len(selected), config.output_path, config.meta_output_path)


def run_aggregation(config: AppConfig) -> None:
    extras = (
        (config.profile_config_file,)
        if isinstance(config.profile_config_file, Path)
        else ()
    )
    with configuration_snapshot(extras):
        _run_aggregation(config)


def _run_aggregation(config: AppConfig) -> None:
    require_local_configuration()
    logger.info("Starting aggregation")

    client = HttpClient()
    started_at = time.perf_counter()
    timings: dict[str, float] = {}

    try:
        source_errors: dict[str, str] = {}
        playlist_configuration = load_playlist_configuration(config.profile_config_file)
        collection = _collect_phase(config, client, timings, source_errors)
        all_channels = collection.channels
        if config.validation_due_only and source_errors:
            raise RuntimeError(
                "Due-only validation requires intact local source caches"
            )
        reference = None
        if config.companion_output_path:
            reference_cache = (
                config.companion_reference_cache_path
                or config.epg_cache_file.parent / ".cache" / "companion-reference.json"
            )
            if config.validation_due_only:
                reference = validate_reference(
                    json.loads(
                        (config.companion_reference_file or reference_cache).read_text()
                    )
                )
            else:
                reference = load_reference(
                    client, reference_cache, local_file=config.companion_reference_file
                )

        xmltv_aliases = _timed(
            timings, "load_epg_aliases", lambda: _load_alias_phase(config, client)
        )
        ledger = DecisionLedger()
        eligibility = _timed(
            timings,
            "enrich_epg_metadata",
            lambda: prepare_candidates(
                all_channels,
                lambda channel: rejection_reason(channel)
                or (None if _transport_safe(channel) else "transport_ineligible"),
                lambda values: canonicalize_channels(
                    enrich_companion_candidates(values, xmltv_aliases)
                ),
                ledger,
            ),
        )
        enriched_channels = eligibility.channels
        if config.profile == "base":
            selected = _timed(
                timings,
                "select_channels",
                lambda: select_candidates(
                    enriched_channels,
                    lambda values: select_profile_channels(
                        values,
                        playlist_configuration,
                        "base",
                        player_profile=config.player_profile,
                    ),
                    lambda channel: (
                        "deduplicated"
                        if _base_candidates(
                            [channel], playlist_configuration.profile("base")
                        )
                        else "profile_policy"
                    ),
                    ledger,
                ).channels,
            )
        else:
            selected = enriched_channels

        selected_before_stream_filter = len(selected)
        validation = validate_candidates(
            selected,
            lambda values: _validate_phase(config, values, timings),
            lambda values: (
                _timed(
                    timings,
                    "filter_media_proof",
                    lambda: filter_media_confirmed_channels(
                        values,
                        status_file=config.stream_status_file,
                        max_age_seconds=config.validation_max_success_age,
                    ),
                )
                if config.require_media_proof
                else values
            ),
            ledger,
        )
        selected = validation.channels
        selected_after_stream_filter = len(selected)
        if config.profile != "base":
            selected = _timed(
                timings,
                "select_channels",
                lambda: select_candidates(
                    selected,
                    lambda values: select_profile_channels(
                        values,
                        playlist_configuration,
                        config.profile,
                        player_profile=config.player_profile,
                    ),
                    lambda channel: (
                        "profile_policy"
                        if not _allowed_by_profile(
                            channel, playlist_configuration.profile(config.profile)
                        )
                        else (
                            "transport_ineligible"
                            if not is_exportable(channel, config.player_profile)
                            else "deduplicated"
                        )
                    ),
                    ledger,
                ).channels,
            )

        guide = _guide_phase(config, client, selected, xmltv_aliases, timings, ledger)
        selected = guide.channels
        for channel in selected:
            ledger.record(channel, "selected", "publication")
        _publish_phase(
            config,
            client,
            selected,
            collection,
            playlist_configuration,
            xmltv_aliases,
            source_errors,
            timings,
            started_at,
            selected_before_stream_filter,
            selected_after_stream_filter,
            ledger,
            reference,
        )
        logger.info(
            "Aggregation finished total_input=%s selected=%s source_errors=%s",
            len(all_channels),
            len(selected),
            len(source_errors),
        )

    finally:
        client.close()
