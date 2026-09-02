import copy
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from legal_iptv.clients import HttpClient
from legal_iptv.io import write_json_atomic
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


def merge_provider_guides(roots: list[ET.Element]) -> ET.Element:
    merged = ET.Element("tv", {"generator-info-name": "local-xmltv-generator"})
    channel_ids: set[str] = set()
    programme_keys: set[tuple[str, str, str]] = set()

    for root in roots:
        for channel in root.findall("channel"):
            channel_id = channel.attrib.get("id")
            if not channel_id or channel_id in channel_ids:
                continue
            channel_ids.add(channel_id)
            merged.append(copy.deepcopy(channel))

        for programme in root.findall("programme"):
            channel_id = programme.attrib.get("channel", "")
            key = (
                channel_id,
                programme.attrib.get("start", ""),
                programme.attrib.get("stop", ""),
            )
            if channel_id not in channel_ids or key in programme_keys:
                continue
            programme_keys.add(key)
            merged.append(copy.deepcopy(programme))
    return merged


def _is_fresh(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified >= datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)


def _write_xml_atomic(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("wb", delete=False, dir=path.parent) as temporary:
        ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _within_lkg(path: Path, lkg_seconds: int) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified >= datetime.now(timezone.utc) - timedelta(seconds=lkg_seconds)


def ensure_guide(
    client: HttpClient,
    *,
    output_path: Path,
    diagnostics_path: Path,
    ttl_seconds: int,
    lkg_seconds: int,
    min_coverage_ratio: float,
    providers: list[EpgProvider] | None = None,
) -> GuideResult:
    if _is_fresh(output_path, ttl_seconds):
        try:
            stats = validate_guide_file(output_path)
            return GuideResult("fresh", stats)
        except (OSError, ET.ParseError, ValueError):
            logger.warning("Fresh guide is invalid; generating a replacement")

    previous_stats: GuideStats | None = None
    if output_path.exists():
        try:
            previous_stats = validate_guide_file(output_path)
        except (OSError, ET.ParseError, ValueError):
            previous_stats = None

    roots: list[ET.Element] = []
    provider_diagnostics: list[dict] = []
    for provider in providers if providers is not None else load_epg_providers():
        try:
            root = provider.fetch(client)
            roots.append(root)
            provider_diagnostics.append({"id": provider.id, "status": "ok"})
        except Exception as exc:
            logger.warning("EPG provider failed id=%s error_type=%s", provider.id, type(exc).__name__)
            provider_diagnostics.append(
                {"id": provider.id, "status": "error", "error_type": type(exc).__name__}
            )

    try:
        if not roots:
            raise RuntimeError("No EPG provider produced a candidate")
        candidate = merge_provider_guides(roots)
        stats = validate_guide_root(candidate)
        if previous_stats and stats.programmes < previous_stats.programmes * min_coverage_ratio:
            raise ValueError("Guide programme count dropped below the configured ratio")
        if previous_stats and stats.channels < previous_stats.channels * min_coverage_ratio:
            raise ValueError("Guide channel count dropped below the configured ratio")
        _write_xml_atomic(output_path, candidate)
        write_json_atomic(
            diagnostics_path,
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "updated",
                "stats": asdict(stats),
                "providers": provider_diagnostics,
            },
        )
        return GuideResult("updated", stats)
    except Exception as exc:
        retained = previous_stats is not None and _within_lkg(output_path, lkg_seconds)
        write_json_atomic(
            diagnostics_path,
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "status": "retained" if retained else "failed",
                "error_type": type(exc).__name__,
                "providers": provider_diagnostics,
            },
        )
        if retained:
            logger.warning("Guide candidate rejected; keeping last-known-good")
            return GuideResult("retained", previous_stats)
        raise
