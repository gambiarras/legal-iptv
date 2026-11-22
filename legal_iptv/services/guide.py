import copy
import gzip
import hashlib
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from legal_iptv.clients import HttpClient
from legal_iptv.io import write_json_atomic
from legal_iptv.local_config import environment_value, runtime_value
from legal_iptv.services.epg_providers import EpgProvider, load_epg_providers

logger = logging.getLogger(__name__)
XMLTV_TIME_PATTERN = re.compile(r"^(\d{8,14})(?:\s+([+-]\d{4}|Z))?$")


@dataclass(slots=True, frozen=True)
class GuideStats:
    channels: int
    programmes: int
    present_programmes: int
    future_programmes: int


@dataclass(slots=True, frozen=True)
class GuideResult:
    status: str
    stats: GuideStats


def parse_xmltv_time(value: str) -> datetime:
    match = XMLTV_TIME_PATTERN.match(value.strip())
    if not match:
        raise ValueError("Invalid XMLTV timestamp")
    digits, offset = match.groups()
    formats = {8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}
    try:
        parsed = datetime.strptime(digits, formats[len(digits)])
    except (KeyError, ValueError) as exc:
        raise ValueError("Invalid XMLTV timestamp") from exc
    if not offset or offset == "Z":
        return parsed.replace(tzinfo=timezone.utc)
    sign = 1 if offset[0] == "+" else -1
    delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
    return parsed.replace(tzinfo=timezone(sign * delta))


def validate_guide_root(root: ET.Element, *, now: datetime | None = None) -> GuideStats:
    if root.tag != "tv":
        raise ValueError("Guide root must be tv")
    now = now or datetime.now(timezone.utc)
    channel_ids = {
        element.attrib["id"]
        for element in root.findall("channel")
        if element.attrib.get("id")
    }
    if not channel_ids:
        raise ValueError("Guide has no channels")

    programmes = root.findall("programme")
    if not programmes:
        raise ValueError("Guide has no programmes")
    present = 0
    future = 0
    for programme in programmes:
        channel_id = programme.attrib.get("channel")
        if channel_id not in channel_ids:
            raise ValueError("Programme references an unknown channel")
        start = parse_xmltv_time(programme.attrib.get("start", ""))
        stop = parse_xmltv_time(programme.attrib.get("stop", ""))
        if stop <= start:
            raise ValueError("Programme stop must be after start")
        if start <= now < stop:
            present += 1
        if start > now:
            future += 1
    if not present or not future:
        raise ValueError("Guide lacks present or future coverage")
    return GuideStats(len(channel_ids), len(programmes), present, future)


def validate_guide_file(path: Path, *, now: datetime | None = None) -> GuideStats:
    return validate_guide_root(ET.parse(path).getroot(), now=now)


def validate_required_channels(root: ET.Element, channel_ids: tuple[str, ...], *,
                               now: datetime | None = None) -> dict[str, dict]:
    """A current programme on some other channel cannot satisfy a priority feed."""
    now = now or datetime.now(timezone.utc)
    result = {}
    known = {channel.get("id") for channel in root.findall("channel")}
    for channel_id in channel_ids:
        programmes = [item for item in root.findall("programme") if item.get("channel") == channel_id]
        windows = [(parse_xmltv_time(item.get("start", "")), parse_xmltv_time(item.get("stop", ""))) for item in programmes]
        present = sum(start <= now < stop for start, stop in windows)
        future = sum(start > now for start, stop in windows)
        result[channel_id] = {"present_programmes": present, "future_programmes": future,
                              "last_stop": max((stop for _, stop in windows), default=now).isoformat()}
        if channel_id not in known or not present or not future:
            raise ValueError(f"Required EPG channel lacks current or future coverage: {channel_id}")
    return result


def _compact_programme(programme: ET.Element) -> ET.Element:
    copied = ET.Element("programme", dict(programme.attrib))
    for child in programme:
        if child.tag in {"title", "sub-title", "category", "episode-num", "icon", "date", "rating"}:
            copied.append(copy.deepcopy(child))
    return copied


def _programme_window(programme: ET.Element, now: datetime, horizon: datetime):
    title = programme.findtext("title", "").strip()
    if not title or title.casefold() in {"no data", "sem programação", "programação indisponível", "tba"}:
        return None
    try:
        start = parse_xmltv_time(programme.get("start", ""))
        stop = parse_xmltv_time(programme.get("stop", ""))
    except ValueError:
        return None
    if stop <= start or stop <= now or start >= horizon:
        return None
    return start, stop


class _LimitedXmlReader:
    def __init__(self, source, maximum: int):
        self.source = source
        self.maximum = maximum
        self.count = 0

    def read(self, size=-1):
        # ElementTree reads incrementally. The bound also applies after gzip
        # expansion, preventing compressed inputs from bypassing byte budgets.
        value = self.source.read(min(size, 65536) if size >= 0 else 65536)
        self.count += len(value)
        if self.count > self.maximum:
            raise ValueError("Decoded XMLTV exceeds the input byte budget")
        return value


def read_xmltv_source(source_path: Path, *, channel_ids: Collection[str] | None = None,
                      now: datetime | None = None, days: int = 3,
                      inventory: dict | None = None) -> ET.Element:
    """Parse upstream incrementally, retaining only selected, dated programmes.

    Channel inventory remains small and independent of output selection; raw
    programmes for unselected channels are cleared as soon as they are read.
    """
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, min(days, 7)))
    wanted = set(channel_ids) if channel_ids is not None else None
    root = ET.Element("tv", {"generator-info-name": "local-xmltv-generator"})
    channels = {}
    programmes = []
    maximum = int(environment_value("IPTV_EPG_UPSTREAM_MAX_BYTES", str(128 * 1024 * 1024)))
    with source_path.open("rb") as raw:
        magic = raw.read(2)
        raw.seek(0)
        stream = gzip.GzipFile(fileobj=raw) if magic == b"\x1f\x8b" else raw
        try:
            parser_root = None
            depth = 0
            for event, element in ET.iterparse(_LimitedXmlReader(stream, maximum), events=("start", "end")):
                if event == "start":
                    depth += 1
                    if parser_root is None:
                        parser_root = element
                        if element.tag != "tv":
                            raise ValueError("EPG source root is not tv")
                    continue
                if depth == 2:
                    if element.tag == "channel":
                        identifier = element.get("id", "")
                        if identifier:
                            if wanted is None or identifier in wanted:
                                channels.setdefault(identifier, copy.deepcopy(element))
                            if inventory is not None and len(inventory) < 50000:
                                record = inventory.setdefault(identifier, {"id": identifier, "names": [], "programme_count": 0, "current": False, "future": False})
                                for name in element.findall("display-name"):
                                    text = (name.text or "").strip()
                                    if text and text not in record["names"] and len(record["names"]) < 8:
                                        record["names"].append(text[:256])
                    elif element.tag == "programme":
                        identifier = element.get("channel", "")
                        window = _programme_window(element, now, horizon)
                        if window is not None:
                            if inventory is not None and identifier in inventory:
                                record = inventory[identifier]
                                record["programme_count"] += 1
                                record["current"] |= window[0] <= now < window[1]
                                record["future"] |= window[0] > now
                            if wanted is None or identifier in wanted:
                                programmes.append(_compact_programme(element))
                    element.clear()
                    parser_root.remove(element)
                depth -= 1
        finally:
            if stream is not raw:
                stream.close()
    root.extend(channels.values())
    root.extend(item for item in programmes if item.get("channel") in channels)
    return root


def merge_provider_guides(roots: list[ET.Element], *, now: datetime | None = None,
                          days: int = 3, channel_ids: Collection[str] | None = None) -> ET.Element:
    """Prefer earlier providers per channel/interval; lower sources fill gaps.

    Only source intervals inside the requested horizon are eligible. A partial
    official schedule remains authoritative where it has dated programmes, while
    an empty/stale schedule cannot mask another source's current programmes.
    """
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, min(days, 7)))
    wanted = set(channel_ids) if channel_ids is not None else None
    channels = {}
    selected: dict[str, list[tuple[datetime, datetime, ET.Element]]] = defaultdict(list)
    clipped = 0
    for root in roots:
        source_channels = {item.get("id") for item in root.findall("channel")}
        for channel in root.findall("channel"):
            identifier = channel.get("id")
            if identifier and (wanted is None or identifier in wanted):
                channels.setdefault(identifier, channel)
        grouped = defaultdict(list)
        for programme in root.findall("programme"):
            identifier = programme.get("channel", "")
            if identifier not in source_channels or (wanted is not None and identifier not in wanted):
                continue
            window = _programme_window(programme, now, horizon)
            if window is not None:
                grouped[identifier].append((*window, programme))
        for identifier, entries in grouped.items():
            occupied = selected[identifier]
            for start, stop, programme in sorted(entries, key=lambda item: item[0]):
                pieces = [(start, stop)]
                for known_start, known_stop, _ in occupied:
                    if known_start >= stop:
                        break
                    if known_stop <= start:
                        continue
                    remaining = []
                    for left, right in pieces:
                        if known_stop <= left or known_start >= right:
                            remaining.append((left, right))
                        else:
                            if left < known_start:
                                remaining.append((left, known_start))
                            if known_stop < right:
                                remaining.append((known_stop, right))
                    pieces = remaining
                    if not pieces:
                        break
                for left, right in pieces:
                    copied = _compact_programme(programme)
                    if (left, right) != (start, stop):
                        copied.set("start", left.strftime("%Y%m%d%H%M%S %z"))
                        copied.set("stop", right.strftime("%Y%m%d%H%M%S %z"))
                        clipped += 1
                    position = bisect_left([item[0] for item in occupied], left)
                    occupied.insert(position, (left, right, copied))
    merged = ET.Element("tv", {"generator-info-name": "local-xmltv-generator", "data-clipped-programmes": str(clipped)})
    for identifier, channel in channels.items():
        merged.append(copy.deepcopy(channel))
    for identifier in channels:
        merged.extend(item for _, _, item in selected.get(identifier, []))
    return merged


def guide_coverage(root: ET.Element, channel_ids: Collection[str], *,
                   now: datetime | None = None, days: int = 3) -> dict[str, dict]:
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, min(days, 7)))
    grouped = defaultdict(list)
    for item in root.findall("programme"):
        window = _programme_window(item, now, horizon)
        if window is not None:
            grouped[item.get("channel")].append(window)
    result = {}
    for identifier in sorted(set(channel_ids)):
        windows = sorted(grouped.get(identifier, []))
        present = sum(start <= now < stop for start, stop in windows)
        future = sum(start > now for start, stop in windows)
        gaps = []
        internal_gaps = 0
        cursor = now
        for start, stop in windows:
            if start > cursor:
                internal_gaps += int(cursor > now)
                gaps.append((cursor.isoformat(), min(start, horizon).isoformat()))
            cursor = max(cursor, min(stop, horizon))
        if cursor < horizon:
            gaps.append((cursor.isoformat(), horizon.isoformat()))
        status = "current_and_future" if present and future else "current_only" if present else "future_only" if future else "missing"
        result[identifier] = {"status": status, "programmes": len(windows), "current_programmes": present,
                              "future_programmes": future, "gap_count": len(gaps), "internal_gap_count": internal_gaps, "gaps": gaps[:10],
                              "last_stop": max((stop for _, stop in windows), default=None).isoformat() if windows else None}
    return result


def coverage_diagnostics(root: ET.Element, channel_ids: Collection[str], *, required_ids: Collection[str] = (),
                         now: datetime | None = None, days: int = 3) -> dict:
    """Summarize all selected identities while bounding the per-channel payload."""
    coverage = guide_coverage(root, channel_ids, now=now, days=days)
    required = set(required_ids)
    summary = {"total_channels": len(coverage),
               "status_counts": dict(sorted(Counter(item["status"] for item in coverage.values()).items())),
               "with_current": sum(bool(item["current_programmes"]) for item in coverage.values()),
               "with_future": sum(bool(item["future_programmes"]) for item in coverage.values()),
               "gap_channels": sum(bool(item["gap_count"]) for item in coverage.values()),
               "required_channels": len(required),
               "required_missing": sum(coverage.get(identifier, {}).get("status") != "current_and_future" for identifier in required)}
    maximum = max(1, min(int(runtime_value("epg_diagnostics_limit", 2000)), 5000))
    ordered = sorted(coverage, key=lambda identifier: (identifier not in required, identifier))
    return {"coverage": {identifier: coverage[identifier] for identifier in ordered[:maximum]},
            "summary": summary, "coverage_truncated": max(0, len(coverage) - maximum)}


def _guide_diagnostics(path: Path, root: ET.Element, ids: Collection[str], *, required_ids: Collection[str] = (),
                       now: datetime, days: int, **details) -> None:
    try:
        previous = json.loads(path.read_text()) if path.is_file() else {}
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    payload = {key: previous[key] for key in ("providers",) if key in previous}
    payload.update(details)
    payload.update(coverage_diagnostics(root, ids, required_ids=required_ids, now=now, days=days))
    payload.update(generated_at=now.isoformat(), window_days=days)
    write_json_atomic(path, payload)


def _provider_targets(provider: EpgProvider) -> set[str] | None:
    if provider.channel_ids:
        return set(provider.channel_ids)
    if provider.format == "channel_schedule":
        identifier = runtime_value("channel_schedule_id")
        return {identifier} if identifier else set()
    if provider.format in {"official_schedule_a", "official_schedule_b"}:
        identifier = runtime_value("official_schedules", {}).get(provider.format, {}).get("id")
        return {identifier} if identifier else set()
    return None


def _targeted_providers(providers: list[EpgProvider], affected: set[str]) -> list[EpgProvider]:
    covered = set()
    selected = []
    broad = []
    for provider in providers:
        targets = _provider_targets(provider) if isinstance(provider, EpgProvider) else None
        if targets is None:
            broad.append(provider)
        elif targets.intersection(affected):
            covered.update(targets.intersection(affected))
            selected.append(provider)
    return selected + (broad if affected - covered else [])


def _guide_progress(callback, phase: str, details: dict) -> None:
    if callback is not None:
        try:
            callback(phase, details)
        except Exception as exc:
            logger.warning("Guide progress callback failed error_type=%s", type(exc).__name__)


def project_guide(source_path: Path, target_path: Path, channel_ids: Collection[str], *,
                  days: int = 3, now: datetime | None = None,
                  required_channel_ids: tuple[str, ...] = (),
                  channel_names: Mapping[str, str] | None = None,
                  diagnostics_path: Path | None = None,
                  require_coverage: bool = True) -> GuideResult:
    """Stream a broad upstream into a single compact public guide.

    source == target is supported. A failed projection leaves the original
    intact. Projection preserves collection mtime so it cannot extend TTL/LKG.
    """
    now = now or datetime.now(timezone.utc)
    collection_stat = source_path.stat()
    collected_at = collection_stat.st_mtime
    loaded = read_xmltv_source(source_path, channel_ids=channel_ids, now=now, days=days)
    candidate = merge_provider_guides([loaded], now=now, days=days, channel_ids=channel_ids)
    declared = {channel.get("id") for channel in candidate.findall("channel")}
    # A playlist ID may have no known schedule. Declare its identity without
    # implying programme coverage; all declarations precede programmes in XMLTV.
    insert_at = len(candidate.findall("channel"))
    for identifier in sorted(set(channel_ids) - declared):
        if not identifier:
            continue
        channel = ET.Element("channel", {"id": identifier})
        ET.SubElement(channel, "display-name").text = (channel_names or {}).get(identifier) or identifier
        candidate.insert(insert_at, channel)
        insert_at += 1
    coverage = guide_coverage(candidate, channel_ids, now=now, days=days)
    if require_coverage:
        stats = validate_guide_root(candidate, now=now)
    else:
        stats = GuideStats(len(candidate.findall("channel")), len(candidate.findall("programme")),
                           sum(item["current_programmes"] for item in coverage.values()),
                           sum(item["future_programmes"] for item in coverage.values()))
    validate_required_channels(candidate, required_channel_ids, now=now)
    candidate.attrib.pop("data-clipped-programmes", None)
    _write_xml_atomic(target_path, candidate)
    os.utime(target_path, ns=(collection_stat.st_atime_ns, collection_stat.st_mtime_ns))
    if diagnostics_path:
        _guide_diagnostics(diagnostics_path, candidate, channel_ids, required_ids=required_channel_ids,
                           now=now, days=days, status="projected", stats=asdict(stats),
                           collected_at=datetime.fromtimestamp(collected_at, timezone.utc).isoformat())
    return GuideResult("projected", stats)



def _is_fresh(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified >= datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)


def _write_xml_atomic(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile("wb", prefix="epg-", suffix=".tmp", delete=False, dir=path.parent) as temporary:
            temporary_path = Path(temporary.name)
            ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _within_lkg(path: Path, lkg_seconds: int) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified >= datetime.now(timezone.utc) - timedelta(seconds=lkg_seconds)


def load_guide_inventory_aliases(cache_dir: Path):
    """Read the private factual index; its timestamp is not a playback guarantee."""
    from legal_iptv.services.epg_mapper import EPGMapping, _normalize

    path = cache_dir / "channel-inventory.json"
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(payload.get("generated_at", ""))
        age = (datetime.now(timezone.utc) - generated).total_seconds()
        if payload.get("version") != 1 or not 0 <= age <= 48 * 3600:
            return {}
        candidates = defaultdict(set)
        for item in payload.get("channels", []):
            if not item.get("id") or not item.get("programme_count"):
                continue
            for name in [item["id"]] + item.get("names", []):
                key = _normalize(name)
                if key:
                    candidates[key].add(item["id"])
        return {key: EPGMapping(next(iter(identifiers))) for key, identifiers in candidates.items()
                if len(identifiers) == 1}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def ensure_guide(
    client: HttpClient,
    *,
    output_path: Path,
    diagnostics_path: Path,
    ttl_seconds: int,
    lkg_seconds: int,
    min_coverage_ratio: float,
    providers: list[EpgProvider] | None = None,
    required_channel_ids: tuple[str, ...] = (),
    selected_channel_ids: Collection[str] | None = None,
    selected_channel_names: dict[str, str] | None = None,
    days: int = 3,
    on_progress: Callable[[str, dict], None] | None = None,
) -> GuideResult:
    from legal_iptv.services.epg_cache import prune_cache

    now = datetime.now(timezone.utc)
    selected = set(selected_channel_ids) if selected_channel_ids is not None else None
    cache_dir = output_path.parent / ".epg-cache"
    prune_cache(cache_dir, now=now)
    selection_path = cache_dir / "guide-selection.json"
    identity = [2, sorted(selected) if selected is not None else None, days,
                sorted((selected_channel_names or {}).items())]
    fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    try:
        previous_fingerprint = json.loads(selection_path.read_text()).get("fingerprint")
    except (OSError, ValueError, AttributeError):
        previous_fingerprint = None
    same_selection = selected is None or previous_fingerprint == fingerprint
    legacy_selection = previous_fingerprint is None
    fresh_base = None
    affected_ids: set[str] | None = None
    collection_time = output_path.stat().st_mtime_ns if output_path.is_file() else None
    previous_stats = None
    previous_programmed_channels = 0
    if output_path.is_file():
        try:
            old_inventory = {} if legacy_selection else None
            loaded = read_xmltv_source(output_path, channel_ids=selected, now=now, days=days,
                                      inventory=old_inventory)
            loaded_stats = validate_guide_root(loaded, now=now)
            coverage = guide_coverage(loaded, selected if selected is not None else
                                      {item.get("id") for item in loaded.findall("channel")}, now=now, days=days)
            missing_required = {identifier for identifier in required_channel_ids
                                if coverage.get(identifier, {}).get("status") != "current_and_future"}
            critical = set(runtime_value("epg_critical_channel_ids", [])) | set(required_channel_ids)
            missing_critical = {identifier for identifier in critical.intersection(coverage)
                                if coverage[identifier]["status"] != "current_and_future" or coverage[identifier]["internal_gap_count"] > 0}
            declared = {item.get("id") for item in loaded.findall("channel")}
            new_ids = set(selected or ()) - declared
            if not missing_required:
                previous_stats = loaded_stats
                previous_programmed_channels = len({item.get("channel") for item in loaded.findall("programme")})
            if _is_fresh(output_path, ttl_seconds):
                affected_ids = missing_required | missing_critical | new_ids
                if not affected_ids and (same_selection or legacy_selection):
                    if legacy_selection:
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        write_json_atomic(selection_path, {"version": 2, "fingerprint": fingerprint})
                        if old_inventory:
                            write_json_atomic(cache_dir / "channel-inventory.json", {
                                "version": 1, "generated_at": now.isoformat(), "channels": list(old_inventory.values())})
                    _guide_diagnostics(diagnostics_path, loaded, coverage, required_ids=required_channel_ids,
                                       now=now, days=days, status="fresh", stats=asdict(loaded_stats))
                    return GuideResult("fresh", loaded_stats)
                fresh_base = loaded
        except (OSError, ET.ParseError, ValueError):
            logger.warning("Existing guide is invalid for the current selection/window")
    roots = []
    provider_diagnostics = []
    selected_providers = providers if providers is not None else load_epg_providers()
    if fresh_base is not None:
        selected_providers = _targeted_providers(selected_providers, affected_ids or set())
    requested_ids = affected_ids if fresh_base is not None else selected
    _guide_progress(on_progress, "planned", {"selected_channels": len(selected or ()),
                     "targeted_channels": len(requested_ids or ()), "providers": len(selected_providers)})
    inventory = {}
    for provider in selected_providers:
        try:
            root = (provider.fetch(client, cache_dir=cache_dir, channel_ids=requested_ids,
                                   channel_names=selected_channel_names, now=now, days=days,
                                   inventory=inventory)
                    if isinstance(provider, EpgProvider) else provider.fetch(client))
            roots.append(root)
            diagnostic = {"id": provider.id, "status": "ok" if root.findall("programme") else "empty"}
            for attribute, key in (("data-granularity", "granularity"), ("data-origin", "origin"),
                                   ("data-cache-origin", "cache_origin"), ("data-collected-at", "collected_at"),
                                   ("data-timezone", "timezone"), ("data-schedule-state", "schedule_state")):
                if root.get(attribute):
                    diagnostic[key] = root.get(attribute)
            for attribute, key in (("data-failed-channels", "failed_channels"),
                                   ("data-partial-channels", "partial_channels"),
                                   ("data-failed-dates", "failed_dates"),
                                   ("data-skipped-channels", "skipped_channels")):
                if root.get(attribute):
                    diagnostic[key] = json.loads(root.get(attribute))
            provider_diagnostics.append(diagnostic)
            _guide_progress(on_progress, "provider", diagnostic)
        except Exception as exc:
            logger.warning("EPG provider failed id=%s error_type=%s", provider.id, type(exc).__name__)
            failure = {"id": provider.id, "status": "error", "error_type": type(exc).__name__}
            provider_diagnostics.append(failure)
            _guide_progress(on_progress, "provider", failure)
    if inventory:
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(cache_dir / "channel-inventory.json", {
            "version": 1, "generated_at": now.isoformat(), "channels": list(inventory.values())})
    if fresh_base is not None:
        roots.append(fresh_base)
    try:
        if not roots:
            raise RuntimeError("No EPG provider produced a candidate")
        candidate = merge_provider_guides(roots, now=now, days=days, channel_ids=selected)
        stats = validate_guide_root(candidate, now=now)
        channel_diagnostics = validate_required_channels(candidate, required_channel_ids, now=now)
        if previous_stats and stats.programmes < previous_stats.programmes * min_coverage_ratio:
            raise ValueError("Guide programme count dropped below the configured ratio")
        programmed_channels = len({item.get("channel") for item in candidate.findall("programme")})
        if previous_stats and programmed_channels < previous_programmed_channels * min_coverage_ratio:
            raise ValueError("Guide channel count dropped below the configured ratio")
        coverage = guide_coverage(candidate, selected if selected is not None else
                                  {item.get("id") for item in candidate.findall("channel")}, now=now, days=days)
        clipped = int(candidate.attrib.pop("data-clipped-programmes", "0"))
        _write_xml_atomic(output_path, candidate)
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(selection_path, {"version": 2, "fingerprint": fingerprint})
        if fresh_base is not None and collection_time is not None:
            os.utime(output_path, ns=(collection_time, collection_time))
        _guide_diagnostics(diagnostics_path, candidate, coverage, required_ids=required_channel_ids,
                           now=now, days=days, status="updated", stats=asdict(stats),
                           providers=provider_diagnostics, required_channels=channel_diagnostics,
                           clipped_programmes=clipped, targeted_refresh=fresh_base is not None,
                           targeted_channel_count=len(requested_ids or ()))
        prune_cache(cache_dir, now=now)
        return GuideResult("updated", stats)
    except Exception as exc:
        retained = previous_stats is not None and _within_lkg(output_path, lkg_seconds)
        if retained:
            _guide_diagnostics(diagnostics_path, loaded, selected if selected is not None else
                               {item.get("id") for item in loaded.findall("channel")}, required_ids=required_channel_ids,
                               now=now, days=days, status="retained", error_type=type(exc).__name__,
                               providers=provider_diagnostics, required_channel_ids=list(required_channel_ids))
        else:
            write_json_atomic(diagnostics_path, {"generated_at": now.isoformat(), "status": "failed",
                              "error_type": type(exc).__name__, "providers": provider_diagnostics,
                              "required_channel_ids": list(required_channel_ids)})
        if retained:
            logger.warning("Guide candidate rejected; keeping last-known-good")
            return GuideResult("retained", previous_stats)
        raise
