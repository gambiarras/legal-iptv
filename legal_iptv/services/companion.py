"""A separate, conservative playlist that complements the visible base reference catalogue."""

from legal_iptv.local_config import runtime_value

import copy
import os
import shutil
import tempfile
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from legal_iptv.clients import HttpClient
from legal_iptv.config import AppConfig
from legal_iptv.exporters import render_m3u
from legal_iptv.io import write_json_atomic, write_text_atomic
from legal_iptv.models import Channel
from legal_iptv.services.companion_reference import load_reference
from legal_iptv.services.channel_identity import canonical_key, load_policy, rejection_reason
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.services.guide import parse_xmltv_time
from legal_iptv.services.epg_mapper import enrich_epg_metadata
from legal_iptv.services.publication import is_exportable
from legal_iptv.services.video_platform_transport import is_public_video_platform_hls



def enrich_companion_candidates(channels: list[Channel], xmltv_aliases: dict) -> list[Channel]:
    """Use guide IDs without discarding original regional/alternative identity."""
    enriched = enrich_epg_metadata(channels, xmltv_aliases=xmltv_aliases)
    return [replace(item, name=original.name) for original, item in zip(channels, enriched)]


def _key(name: str, policy: dict) -> str:
    return canonical_key(name, policy)


def _transport_safe(channel: Channel) -> bool:
    try:
        if not is_exportable(channel, "portable"):
            return False
        parsed = urlsplit(channel.stream_url)
        # Account paths remain private; only verified renewable media has a narrow exception.
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        settings = runtime_value("video_platform", {})
        platform_hosts = tuple(settings.get("media_hosts", []) + settings.get("page_hosts", [])
                               + settings.get("short_hosts", []))
        if (parsed.hostname.endswith(platform_hosts) and not is_public_video_platform_hls(channel)):
            return False
        if re.search(r"/(?:live|movie|series)/[^/]+/[^/]+/", parsed.path, re.I):
            return False
        if parsed.path.casefold().endswith((".html", ".htm", "/get.php")):
            return False
    except ValueError:
        return False
    return channel.protocol not in {"embed", "video_platform"}


def select_companion_channels(channels: list[Channel], reference: dict,
                              policy: dict | None = None, *,
                              preselected: bool = False) -> tuple[list[Channel], list[dict]]:
    """Subtract Base Reference identities from the same winners used by the main list."""
    policy = policy or load_policy()
    base_reference = {_key(name, policy) for name in reference["base_reference_channels"]}
    main_only = set(policy.get("companion_excluded_ids", []))
    excluded = []
    eligible = []
    for channel in channels:
        reason = ("main_only" if main_only.intersection(
            {channel.id, channel.tvg_id, channel.logical_channel_id}) else rejection_reason(channel, policy))
        if not reason and (channel.removed or channel.status in {"offline", "removed"}):
            reason = "unavailable"
        if not reason and not _transport_safe(channel):
            reason = "not_portable_or_sensitive"
        if not reason and _key(channel.name, policy) in base_reference:
            reason = "already_in_base_reference"
        if reason:
            excluded.append({"id": channel.id, "name": channel.name, "reason": reason})
        else:
            eligible.append(channel)
    selected = (eligible if preselected else select_profile_channels(
        eligible, load_playlist_configuration(), "full"))
    return sorted(selected, key=lambda item: (item.group.casefold(), item.name.casefold())), excluded


def build_companion_guide(guide_file: Path, channels: list[Channel], *,
                          required_ids: tuple[str, ...] | None = None,
                          now: datetime | None = None) -> tuple[ET.Element, dict]:
    now = now or datetime.now(timezone.utc)
    ids = {channel.tvg_id for channel in channels if channel.tvg_id}
    if required_ids is None:
        required_ids = tuple(load_policy().get("required_channel_ids", []))
    if not set(required_ids).issubset(ids):
        raise ValueError("Companion required channel is absent from the playlist")
    root = ET.Element("tv", {"generator-info-name": "Companion"})
    declarations: dict[str, ET.Element] = {}
    programmes: list[ET.Element] = []
    coverage = {value: {"programmes": 0, "current": False, "future": False,
                        "horizon_at": None} for value in sorted(ids)}
    horizon = now + timedelta(days=3)
    context = ET.iterparse(guide_file, events=("start", "end"))
    _, original_root = next(context)
    for event, element in context:
        if event != "end":
            continue
        if element.tag == "channel":
            channel_id = element.get("id")
            if channel_id in ids:
                declarations[channel_id] = copy.deepcopy(element)
            element.clear()
            original_root.clear()
        elif element.tag == "programme":
            channel_id = element.get("channel")
            if channel_id in ids:
                try:
                    start = parse_xmltv_time(element.get("start", ""))
                    stop = parse_xmltv_time(element.get("stop", ""))
                except (ValueError, TypeError):
                    element.clear()
                    original_root.clear()
                    continue
                if stop > max(start, now) and start < horizon and element.findtext("title"):
                    programmes.append(copy.deepcopy(element))
                    stats = coverage[channel_id]
                    stats["programmes"] += 1
                    stats["current"] = stats["current"] or start <= now < stop
                    stats["future"] = stats["future"] or start > now
                    stop_iso = stop.astimezone(timezone.utc).isoformat()
                    stats["horizon_at"] = max(stats["horizon_at"] or stop_iso, stop_iso)
            element.clear()
            original_root.clear()
    for channel_id in sorted(ids):
        declaration = declarations.get(channel_id)
        if declaration is None:
            declaration = ET.Element("channel", {"id": channel_id})
            ET.SubElement(declaration, "display-name").text = next(
                channel.name for channel in channels if channel.tvg_id == channel_id)
        root.append(declaration)
    root.extend(programmes)
    for channel_id in required_ids:
        if not coverage[channel_id]["current"] or not coverage[channel_id]["future"]:
            raise ValueError("Companion required EPG lacks present or future coverage")
    return root, coverage


def run_companion(channels: list[Channel], config: AppConfig, client: HttpClient, *,
                  reference: dict | None = None) -> dict:
    companion_paths = (config.companion_output_path, config.companion_meta_output_path,
                       config.companion_guide_output_path)
    if not all(companion_paths):
        raise ValueError("Companion requires separate playlist, metadata and guide outputs")
    outputs = [config.output_path, config.meta_output_path, config.guide_output_path,
               *companion_paths]
    if len({path.resolve() for path in outputs}) != len(outputs):
        raise ValueError("Companion output paths must be distinct from the main outputs")
    policy = load_policy()
    reference = reference or load_reference(
        client, config.companion_reference_cache_path or
        config.epg_cache_file.parent / ".cache" / "companion-reference.json",
        local_file=config.companion_reference_file,
    )
    selected, excluded = select_companion_channels(channels, reference, policy, preselected=True)
    required = tuple(value for value in policy["required_channel_ids"]
                     if any(channel.tvg_id == value for channel in selected))
    _, coverage = build_companion_guide(config.guide_output_path, selected, required_ids=required)
    configuration = load_playlist_configuration(config.profile_config_file)
    guide_url = config.guide_url or configuration.guide_url or policy["guide_url"]
    playlist = render_m3u(
        selected, guide_url=guide_url, player_profile=config.player_profile,
        category_order=configuration.group_order, legacy_guide_urls=True,
        enforce_capabilities=True,
    )
    metadata = {
        "name": policy["name"], "schema_version": 2,
        "guide_url": guide_url, "selection": "main_minus_base_reference",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_channels": len(selected),
        "required_epg_ids": list(required), "epg_coverage": coverage,
        "reference_generated_at": reference["generated_at"],
        "reference_retained": reference.get("retained_reference", False),
        "base_reference_channels": len(reference["base_reference_channels"]),
        "directory_catalog_reference_channels": len(reference["directory_catalog_channels"]),
        "excluded_by_reason": dict(sorted(Counter(row["reason"] for row in excluded).items())),
        "excluded_channels": excluded,
        "channels": [{"id": item.id, "name": item.name, "tvg_id": item.tvg_id,
                      "provider_id": item.provider_id or item.source,
                      "epg_current": coverage[item.tvg_id]["current"],
                      "epg_future": coverage[item.tvg_id]["future"]} for item in selected],
        "limitations": ["Membership is not proof of stream playback or rights",
                        "Renewable media URLs expire and need periodic refresh",
                        "Schedule coverage depends on source availability"],
    }
    # Validate and construct the whole bundle before changing any Companion output.
    write_guide_alias(config.guide_output_path, config.companion_guide_output_path)
    write_text_atomic(config.companion_output_path, playlist)
    write_json_atomic(config.companion_meta_output_path, metadata)
    return metadata


def write_guide_alias(source: Path, target: Path) -> None:
    """Compatibility name for the same guide, without a second physical copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".guide-alias-", dir=target.parent)
    os.close(handle)
    temporary = Path(name)
    try:
        temporary.unlink()
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
