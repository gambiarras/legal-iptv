"""A separate, conservative playlist that complements the visible Saimo catalogue."""

import copy
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from legal_iptv.clients import HttpClient
from legal_iptv.config import AppConfig
from legal_iptv.exporters import render_m3u
from legal_iptv.io import write_json_atomic, write_text_atomic
from legal_iptv.models import Channel
from legal_iptv.services.companion_reference import identity_key, load_reference
from legal_iptv.services.guide import parse_xmltv_time
from legal_iptv.services.epg_mapper import enrich_epg_metadata
from legal_iptv.services.link_validator import filter_cached_offline_channels
from legal_iptv.services.publication import is_exportable
from legal_iptv.sources.live_stream_catalog import LIVE_STREAM_CATALOG_URL

ALTERNATIVE = re.compile(r"\b(?:alternativ[oa]s?|alternative|backup|reserva)\b", re.I)
ADULT = re.compile(r"(?:\b(?:xxx|porn|adultos?|adults?|nsfw)\b|\+18|18\+)", re.I)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")


def load_policy() -> dict:
    return json.loads(resources.files("legal_iptv.resources").joinpath(
        "companion_policy.json").read_text(encoding="utf-8"))


def enrich_companion_candidates(channels: list[Channel], xmltv_aliases: dict) -> list[Channel]:
    """Use guide IDs without discarding original regional/alternative identity."""
    enriched = enrich_epg_metadata(channels, xmltv_aliases=xmltv_aliases)
    return [replace(item, name=original.name) for original, item in zip(channels, enriched)]


def _key(name: str, policy: dict) -> str:
    key = identity_key(name)
    return policy["aliases"].get(key, key)


def _frost_or_unreviewed(channel: Channel, policy: dict) -> bool:
    provenance = " ".join(str(value or "") for value in (
        channel.provider_id, channel.source_type, channel.id,
        channel.source_url, channel.stream_url,
    )).casefold()
    return any(part in provenance for part in policy["excluded_provider_fragments"])


def _paid(name: str, policy: dict) -> bool:
    key = identity_key(name)
    return any(key == prefix or (
        (len(prefix) >= 4 or prefix in {"hbo", "tnt", "axn", "gnt", "tlc", "amc"})
        and key.startswith(prefix)
    ) for prefix in policy["paid_name_prefixes"])


def _transport_safe(channel: Channel) -> bool:
    try:
        if not is_exportable(channel, "portable"):
            return False
        parsed = urlsplit(channel.stream_url)
        # Xtream account paths and signed YouTube media never belong in this public output.
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.hostname.endswith(("googlevideo.com", "youtube.com", "youtu.be")):
            return False
        if re.search(r"/(?:live|movie|series)/[^/]+/[^/]+/", parsed.path, re.I):
            return False
        if parsed.path.casefold().endswith((".html", ".htm", "/get.php")):
            return False
    except ValueError:
        return False
    return channel.protocol not in {"embed", "youtube"}


def _score(channel: Channel, policy: dict) -> tuple[int, int, int]:
    provider = channel.provider_id or channel.source
    priority = (800 if provider in policy["batista_provider_ids"] else
                750 if provider in {"televisao", "televisao_tv"} else
                600 if channel.source == "iptv_org" else 500)
    quality = " ".join(str(v or "") for v in (channel.resolution, channel.variant_label, channel.name)).casefold()
    resolution = 4 if re.search(r"4k|2160", quality) else 3 if re.search(r"fhd|1080", quality) else 2 if re.search(r"\bhd\b|720", quality) else 1
    return priority, resolution, channel.bitrate or 0


def select_companion_channels(channels: list[Channel], reference: dict,
                              policy: dict | None = None) -> tuple[list[Channel], list[dict]]:
    policy = policy or load_policy()
    saimo = {_key(name, policy) for name in reference["saimo_channels"]}
    directory = {_key(name, policy) for name in reference["televisao_channels"]}
    selected: dict[str, Channel] = {}
    excluded: list[dict] = []
    for channel in channels:
        key = _key(channel.name, policy)
        text = " ".join(str(value or "") for value in (channel.name, channel.group, channel.raw_group))
        provider = channel.provider_id or channel.source
        reason = None
        if _frost_or_unreviewed(channel, policy):
            reason = "excluded_provider"
        elif channel.removed or channel.status in {"offline", "removed"}:
            reason = "unavailable"
        elif channel.is_adult or ADULT.search(text):
            reason = "adult"
        elif ALTERNATIVE.search(text):
            reason = "alternative"
        elif _paid(channel.name, policy):
            reason = "paid_channel"
        elif key in saimo:
            reason = "already_in_saimo"
        elif "radio" in identity_key(channel.raw_group or channel.group):
            reason = "radio"
        elif not _transport_safe(channel):
            reason = "not_portable_or_sensitive"
        elif not (
            channel.id in policy["approved_extra_ids"]
            or (key in directory and (
                provider in policy["batista_provider_ids"]
                or provider in {"televisao", "televisao_tv"} or channel.source in {"iptv_org", "extra"}
            ))
        ):
            reason = "not_in_reviewed_public_selection"
        if reason:
            excluded.append({"id": channel.id, "name": channel.name, "reason": reason})
            continue
        current = selected.get(key)
        if current is not None and _score(current, policy) >= _score(channel, policy):
            excluded.append({"id": channel.id, "name": channel.name, "reason": "duplicate_variant"})
            continue
        if current is not None:
            excluded.append({"id": current.id, "name": current.name, "reason": "duplicate_variant"})
        tvg_id = "FoxKids.br" if channel.id == "fkids.br" or key == "foxkids" else (
            channel.tvg_id or "Companion." + key + ".br")
        selected[key] = replace(channel, tvg_id=tvg_id)
    # Cross-provider aliases can share the same transport: publish it only once.
    by_url: dict[str, Channel] = {}
    for channel in sorted(selected.values(), key=lambda item: _score(item, policy), reverse=True):
        if channel.stream_url in by_url:
            excluded.append({"id": channel.id, "name": channel.name, "reason": "duplicate_transport"})
        else:
            by_url[channel.stream_url] = channel
    return sorted(by_url.values(), key=lambda item: (item.group.casefold(), item.name.casefold())), excluded


def build_companion_guide(guide_file: Path, channels: list[Channel], *,
                          required_ids: tuple[str, ...] = ("FoxKids.br",),
                          now: datetime | None = None) -> tuple[ET.Element, dict]:
    now = now or datetime.now(timezone.utc)
    ids = {channel.tvg_id for channel in channels if channel.tvg_id}
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


def youtube_channels(raw: list[dict], reference: dict, *, policy: dict | None = None,
                     now: datetime | None = None) -> tuple[list[Channel], list[dict]]:
    policy = policy or load_policy()
    now = now or datetime.now(timezone.utc)
    saimo = {_key(name, policy) for name in reference["saimo_channels"]}
    chosen: dict[str, Channel] = {}
    skipped = []
    for item in raw:
        if not isinstance(item, dict) or item.get("source_type") != "youtube":
            continue
        name = str(item.get("name", "")).split(" - ", 1)[0].strip()
        key = _key(name, policy)
        # Caze has alternate display names in discovery sources.
        is_caze = "caze" in identity_key(str(item.get("id", ""))) or key == "cazetv"
        if key in saimo or is_caze:
            skipped.append({"name": name, "reason": "already_in_saimo"})
            continue
        reason = None
        try:
            parsed = urlsplit(str(item.get("source_url") or ""))
            video_id = (parsed.path.strip("/") if parsed.hostname == "youtu.be"
                        else parse_qs(parsed.query).get("v", [""])[0])
            checked = datetime.fromisoformat(str(item.get("resolved_at") or "").replace("Z", "+00:00"))
            if checked.tzinfo is None:
                raise ValueError("Missing timezone")
            age = (now - checked).total_seconds()
            if (parsed.hostname not in {"youtu.be", "youtube.com", "www.youtube.com"}
                    or not VIDEO_ID.fullmatch(video_id) or not -300 <= age <= 21600
                    or item.get("status") != "resolved" or item.get("removed")):
                reason = "not_a_recent_resolved_video"
        except (ValueError, TypeError):
            reason = "not_a_recent_resolved_video"
        if ADULT.search(name) or "radio" in identity_key(str(item.get("name", ""))):
            reason = "not_a_television_live"
        if reason:
            skipped.append({"name": name, "reason": reason})
            continue
        logical_id = str(item.get("logical_channel_id") or key)
        if logical_id in chosen:
            continue
        chosen[logical_id] = Channel(
            id=str(item.get("id")), name=name, logo=str(item.get("logo") or ""),
            stream_url="plugin://plugin.video.youtube/play/?video_id=" + video_id,
            source="youtube", group="YouTube", tvg_id="Companion.YouTube." + key,
        )
    return sorted(chosen.values(), key=lambda item: item.name.casefold()), skipped


def run_companion(channels: list[Channel], config: AppConfig, client: HttpClient) -> dict:
    companion_paths = (config.companion_output_path, config.companion_meta_output_path,
                       config.companion_guide_output_path)
    if not all(companion_paths):
        raise ValueError("Companion requires separate playlist, metadata and guide outputs")
    outputs = [config.output_path, config.meta_output_path, config.guide_output_path,
               *companion_paths]
    if config.companion_youtube_output_path:
        outputs.append(config.companion_youtube_output_path)
    if len({path.resolve() for path in outputs}) != len(outputs):
        raise ValueError("Companion output paths must be distinct from the main outputs")
    policy = load_policy()
    reference = load_reference(
        client, config.companion_reference_cache_path or
        config.epg_cache_file.parent / ".cache" / "companion-reference.json",
        local_file=config.companion_reference_file,
    )
    candidates = filter_cached_offline_channels(
        channels, status_file=config.stream_status_file,
        max_age_seconds=config.stream_status_max_age,
    )
    selected, excluded = select_companion_channels(candidates, reference, policy)
    required = tuple(policy["required_channel_ids"])
    guide, coverage = build_companion_guide(config.guide_output_path, selected, required_ids=required)
    raw = (json.loads(config.live_catalog_file.read_text(encoding="utf-8"))
           if config.live_catalog_file else client.get_json(LIVE_STREAM_CATALOG_URL))
    youtube, youtube_skipped = youtube_channels(raw, reference, policy=policy)
    playlist = render_m3u(
        selected, guide_url=config.companion_guide_url or policy["guide_url"],
        category_order=tuple(dict.fromkeys(channel.group for channel in selected)),
        legacy_guide_urls=False, include_primary_guide=False, enforce_capabilities=True,
    )
    youtube_playlist = render_m3u(
        youtube, category_order=("YouTube",), legacy_guide_urls=False,
        include_primary_guide=False,
    )
    metadata = {
        "name": policy["name"], "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selected_channels": len(selected), "youtube_selected_channels": len(youtube),
        "required_epg_ids": list(required), "epg_coverage": coverage,
        "reference_generated_at": reference["generated_at"],
        "reference_retained": reference.get("retained_reference", False),
        "saimo_channels": len(reference["saimo_channels"]),
        "televisao_reference_channels": len(reference["televisao_channels"]),
        "excluded_by_reason": dict(sorted(Counter(row["reason"] for row in excluded).items())),
        "excluded_channels": excluded,
        "channels": [{"id": item.id, "name": item.name, "tvg_id": item.tvg_id,
                      "provider_id": item.provider_id or item.source,
                      "epg_current": coverage[item.tvg_id]["current"],
                      "epg_future": coverage[item.tvg_id]["future"]} for item in selected],
        "youtube": {"player": "Kodi with configured YouTube addon", "channels": [
            {"id": item.id, "name": item.name} for item in youtube], "skipped": youtube_skipped},
        "limitations": ["Membership is not proof of stream playback or rights",
                        "YouTube requires a client resolver and is excluded from the portable playlist",
                        "Saimo Android does not import EPG for Minhas listas"],
    }
    # Validate and construct the whole bundle before changing any Companion output.
    guide_xml = ET.tostring(guide, encoding="utf-8", xml_declaration=True).decode()
    write_text_atomic(config.companion_guide_output_path, guide_xml + "\n")
    write_text_atomic(config.companion_output_path, playlist)
    if config.companion_youtube_output_path:
        write_text_atomic(config.companion_youtube_output_path, youtube_playlist)
    write_json_atomic(config.companion_meta_output_path, metadata)
    return metadata
