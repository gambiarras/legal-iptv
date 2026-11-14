"""Public schedule adapters. No playback URL or downloaded code is evaluated."""

import copy
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date, datetime, time as day_time, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from legal_iptv.clients import HttpClient

logger = logging.getLogger(__name__)
FOXKIDS_ID = "FoxKids.br"
FOXKIDS_PAGE = "https://fkidsweb.blogspot.com/p/programacao.html"
FOXKIDS_XMLTV = "https://raw.githubusercontent.com/listamake/iptvbr/main/guide/epg.xml"
FOXKIDS_CATALOG = "https://raw.githubusercontent.com/listamake/sitefkweb/main/catlog.xml"
SCHEDULE_ZONE = ZoneInfo("America/Sao_Paulo")
WEEKDAYS = {name: index for index, name in enumerate(("seg", "ter", "qua", "qui", "sex", "sab", "dom"))}
MAX_PAYLOAD = 25 * 1024 * 1024


def cached_payload(client: HttpClient, url: str, *, cache_dir: Path | None,
                   ttl_seconds: int, now: datetime, headers: dict | None = None) -> bytes:
    """Reuse recent source bytes only; outages never extend their freshness."""
    target = cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".data") if cache_dir else None
    if target and target.is_file():
        age = now.timestamp() - target.stat().st_mtime
        if 0 <= age < ttl_seconds:
            payload = target.read_bytes()
            if 0 < len(payload) <= MAX_PAYLOAD:
                return payload
    payload = client.get_bytes(url, headers=headers)
    if not payload or len(payload) > MAX_PAYLOAD:
        raise ValueError("EPG source payload is empty or too large")
    if target:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile("wb", dir=target.parent, delete=False) as temporary:
                temporary.write(payload)
                temporary_path = Path(temporary.name)
            temporary_path.replace(target)
        except OSError:
            logger.warning("EPG collector cache could not be written")
    return payload


def _xmltv_time(value: str) -> datetime:
    # The published source uses full XMLTV timestamps with explicit UTC offset.
    return datetime.strptime(value, "%Y%m%d%H%M%S %z")


def has_coverage(root: ET.Element, channel_id: str, now: datetime) -> bool:
    present = future = False
    for programme in root.findall("programme"):
        if programme.get("channel") != channel_id:
            continue
        try:
            start = _xmltv_time(programme.get("start", ""))
            stop = _xmltv_time(programme.get("stop", ""))
        except ValueError:
            continue
        if stop <= start or not programme.findtext("title", "").strip():
            continue
        present |= start <= now < stop
        future |= start > now
    return present and future


def _fox_xmltv(payload: bytes, now: datetime) -> ET.Element:
    upstream = ET.fromstring(payload)
    if upstream.tag != "tv":
        raise ValueError("Fox Kids source is not XMLTV")
    root = ET.Element("tv", {"source-info-url": FOXKIDS_PAGE, "data-granularity": "programme"})
    for element in upstream:
        attribute = "id" if element.tag == "channel" else "channel"
        if element.tag not in {"channel", "programme"} or element.get(attribute, "").casefold() not in {"fkids.br", "foxkids.br"}:
            continue
        copied = copy.deepcopy(element)
        copied.set(attribute, FOXKIDS_ID)
        if copied.tag == "programme":
            start = _xmltv_time(copied.get("start", ""))
            stop = _xmltv_time(copied.get("stop", ""))
            if stop <= start or stop <= now:
                continue
        root.append(copied)
    if not any(channel.get("id") == FOXKIDS_ID for channel in root.findall("channel")) or not has_coverage(root, FOXKIDS_ID, now):
        raise ValueError("Published Fox Kids XMLTV lacks current and future coverage")
    return root


@dataclass(frozen=True)
class Slot:
    start: datetime
    stop: datetime
    title: str
    description: str = ""
    category: str = ""
    icon: str = ""
    priority: tuple = ()
    granularity: str = "programme"


def _hours(element: ET.Element) -> list[tuple[int, int, int, set[int], str]]:
    slots = []
    for hour in element.findall("hour"):
        raw = (hour.text or "").strip()
        if not re.fullmatch(r"\d{6}", raw):
            raise ValueError("Fox Kids schedule has an invalid clock")
        clock = day_time(int(raw[:2]), int(raw[2:4]), int(raw[4:6]))
        raw_days = hour.get("days", "todos").casefold()
        days = set(range(7)) if raw_days == "todos" else {WEEKDAYS[name.strip()] for name in raw_days.split(",")}
        slots.append((clock.hour, clock.minute, clock.second, days, hour.get("type", "o")))
    return slots


def _origins_until(start: date, target: date, clock: day_time, hours: list[tuple]) -> int:
    origins = [item for item in hours if item[4] == "o"] or hours
    by_weekday = [{day_time(*item[:3]) for item in origins if weekday in item[3]}
                  for weekday in range(7)]
    difference = (target - start).days
    weeks, remainder = divmod(max(0, difference), 7)
    count = weeks * sum(len(clocks) for clocks in by_weekday)
    for offset in range(remainder):
        weekday = (start.weekday() + offset) % 7
        count += len(by_weekday[weekday])
    return count + sum(value <= clock for value in by_weekday[target.weekday()])


@dataclass(frozen=True)
class _Cycle:
    start: date | None
    repeat: int
    hours: list[tuple]


@dataclass(frozen=True)
class _CatalogEntry:
    id: str
    title: str
    kind: str
    duration: int
    description: str
    category: str
    icon: str
    index: int
    cycles: list[_Cycle]
    episode_count: int
    associations: frozenset[str]

    def cycle_on(self, day: date) -> _Cycle | None:
        active = [cycle for cycle in self.cycles if cycle.start is None or cycle.start <= day]
        return active[-1] if active else None


@dataclass(frozen=True)
class _Occurrence:
    entry: _CatalogEntry
    cycle: _Cycle
    slot: Slot


def _catalog_entries(catalog: ET.Element) -> list[_CatalogEntry]:
    definitions = {item.get("id"): item for item in catalog.findall("prog")}
    episodes: dict[str, list[str]] = {}
    for item in catalog.findall("programme"):
        code = item.findtext("episode-num", "").strip()
        if item.get("progid") and code:
            episodes.setdefault(item.get("progid"), []).append(code)

    def episode_codes(identifier: str, visiting: frozenset[str] = frozenset()) -> list[str]:
        if identifier in visiting:
            raise ValueError("Fox Kids catalog has a cyclic mirror")
        if identifier in episodes:
            return episodes[identifier]
        definition = definitions.get(identifier)
        if definition is None or definition.get("type") != "mirror":
            return []
        association = definition.find("block/ass")
        if association is None:
            return []
        inherited = episode_codes(association.get("id", ""), visiting | {identifier})
        selected = association.get("eps", "").strip()
        if not selected:
            return inherited
        if "-" in selected:
            first, last = selected.split("-", 1)
            return [code for code in inherited if first.strip() <= code <= last.strip()]
        return [code for code in inherited if code in {part.strip() for part in selected.split(",")}]

    entries = []
    for index, item in enumerate(catalog.findall("prog")):
        if item.get("onair", "yes").casefold() == "no":
            continue
        title = item.findtext("display-name", "").strip()
        if not title:
            continue
        categories = [category.text or "" for category in item.findall("category")]
        kind = item.get("type", "")
        kind = "movie" if "Movie" in categories else kind if kind in {"block", "event"} else "series"
        own_hours = _hours(item)
        dated = sorted(item.findall("date"), key=lambda block: block.get("start", ""))
        if not own_hours and not dated:
            # Its parent block remains useful, but no exact series start is
            # invented from the ordering of block associations.
            continue
        length = item.find("length")
        if length is None or length.get("units", "minutes") != "minutes":
            raise ValueError("Fox Kids schedule lacks an explicit duration")
        duration = int(length.text or "0")
        if not 0 < duration <= 48 * 60:
            raise ValueError("Fox Kids schedule duration is outside the supported window")
        cycles = []
        for block in dated or [item]:
            start = block.get("start", item.get("start", ""))
            repeats = int(block.get("repeat", item.get("repeat", "1")))
            if not 1 <= repeats <= 1000:
                raise ValueError("Fox Kids schedule repeat count is invalid")
            cycles.append(_Cycle(datetime.strptime(start, "%Y%m%d").date() if start else None,
                                 repeats, _hours(block) or own_hours))
        icon = item.find("icon")
        entries.append(_CatalogEntry(item.get("id", ""), title, kind, duration,
            item.findtext("desc", "").strip(), ", ".join(categories),
            icon.get("src", "") if icon is not None else "", index, cycles,
            len(episode_codes(item.get("id", ""))),
            frozenset(association.get("id", "") for association in item.findall("block/ass"))))
    return entries


def _occurrence(entry: _CatalogEntry, cycle: _Cycle, day: date, clock: tuple) -> _Occurrence:
    start = datetime.combine(day, day_time(*clock[:3]), SCHEDULE_ZONE)
    start_text = cycle.start.strftime("%Y%m%d") if cycle.start else ""
    rank = 30 if entry.kind == "event" else 20 if entry.kind in {"movie", "series"} else 10
    slot = Slot(start, start + timedelta(minutes=entry.duration), entry.title,
                entry.description, entry.category, entry.icon,
                (rank, start_text, entry.index), "series" if entry.kind == "series" else
                "block" if entry.kind == "block" else "programme")
    return _Occurrence(entry, cycle, slot)


def _fixed_occurrences(entries: list[_CatalogEntry], day: date) -> list[_Occurrence]:
    result = []
    for entry in entries:
        if entry.kind == "series":
            continue
        cycle = entry.cycle_on(day)
        if cycle is None:
            continue
        for clock in cycle.hours:
            if day.weekday() not in clock[3]:
                continue
            if entry.kind == "movie" and (cycle.start is None or (day - cycle.start).days >= cycle.repeat):
                continue
            if entry.kind == "event" and cycle.start:
                if _origins_until(cycle.start, day, day_time(*clock[:3]), cycle.hours) > cycle.repeat:
                    continue
            result.append(_occurrence(entry, cycle, day, clock))
    return result


def _declared_catalog_slots(catalog: ET.Element, *, now: datetime, days: int) -> list[Slot]:
    entries = _catalog_entries(catalog)
    today = now.astimezone(SCHEDULE_ZONE).date()
    horizon = now + timedelta(days=max(1, min(days, 7)))
    starts = [cycle.start for entry in entries if entry.kind == "series"
              for cycle in entry.cycles if cycle.start and cycle.start <= today]
    first_day = min(starts + [today - timedelta(days=2)])
    if (today - first_day).days > 3660:
        raise ValueError("Fox Kids recurring schedule exceeds the supported history")
    # A cycle counts only actual original broadcasts; cancellation by a newer
    # show does not consume its episode budget. We publish the series title,
    # never an inferred episode number or subtitle.
    state: dict[tuple[str, date | None], list] = {}
    result = []
    previous_fixed = _fixed_occurrences(entries, first_day - timedelta(days=1))
    for offset in range((horizon.astimezone(SCHEDULE_ZONE).date() - first_day).days + 1):
        day = first_day + timedelta(days=offset)
        fixed = _fixed_occurrences(entries, day)
        processed = fixed + [item for item in previous_fixed if item.slot.stop.date() >= day]
        series = [(entry, entry.cycle_on(day)) for entry in entries if entry.kind == "series"]
        series = [(entry, cycle) for entry, cycle in series if cycle is not None]
        series.sort(key=lambda pair: (pair[1].start or date.min, pair[0].index), reverse=True)
        active_series = []
        for entry, cycle in series:
            count, last_broadcast, exhausted = state.get((entry.id, cycle.start), [0, False, False])
            budget = entry.episode_count * cycle.repeat if cycle.start and entry.episode_count else None
            origin_cancelled = original_today = False
            daily_hours = sorted((clock for clock in cycle.hours if day.weekday() in clock[3]),
                                 key=lambda clock: clock[:3])
            for clock in daily_hours:
                occurrence = _occurrence(entry, cycle, day, clock)
                overlaps = [item for item in processed if item.slot.start < occurrence.slot.stop and occurrence.slot.start < item.slot.stop]
                event = next((item for item in overlaps if item.entry.kind == "event"), None)
                associated = event is not None and entry.id in event.entry.associations
                newer = any(item.entry.kind != "event" and item.cycle.start is not None and
                            item.cycle.start > (cycle.start or date.min) for item in overlaps)
                if event is not None and not associated:
                    origin_cancelled |= clock[4] == "o"
                    continue
                if event is None and newer:
                    continue
                original = clock[4] == "o" or (origin_cancelled and not original_today)
                if budget is None:
                    valid = True
                elif original:
                    valid = count < budget
                    if valid:
                        count += 1
                        last_broadcast = original_today = True
                        origin_cancelled = False
                    else:
                        # Once the next original is no longer scheduled by the
                        # declared finite cycle, do not repeat its last episode
                        # indefinitely into the future.
                        last_broadcast = False
                        exhausted = True
                else:
                    valid = last_broadcast and not exhausted
                if valid and event is None:
                    active_series.append(occurrence)
                    processed.append(occurrence)
            state[(entry.id, cycle.start)] = [count, last_broadcast, exhausted]
        for occurrence in fixed + active_series:
            if occurrence.slot.stop > now and occurrence.slot.start < horizon:
                result.append(occurrence.slot)
        previous_fixed = fixed
    return result


def foxkids_catalog_to_xmltv(payload: bytes, *, now: datetime, days: int = 3) -> ET.Element:
    """Expand only dates/times declared by the site's catalog.

    Blocks retain their source titles. Episode sequencing is deliberately not
    inferred. Dated films and finite special events may override a block; no
    missing interval is filled, and no old XMLTV date is shifted.
    """
    catalog = ET.fromstring(payload)
    if catalog.tag != "catalog":
        raise ValueError("Fox Kids source is not a schedule catalog")
    candidates = _declared_catalog_slots(catalog, now=now, days=days)
    # Overlay the declared source intervals, newest block/event wins. Boundaries
    # come only from source starts/stops, so gaps remain gaps.
    boundaries = sorted({point for slot in candidates for point in (slot.start, slot.stop)})
    selected: list[Slot] = []
    for start, stop in zip(boundaries, boundaries[1:]):
        covering = [slot for slot in candidates if slot.start <= start and stop <= slot.stop]
        if not covering:
            continue
        winner = max(covering, key=lambda item: item.priority)
        if selected and selected[-1].stop == start and selected[-1].priority == winner.priority:
            previous = selected.pop()
            selected.append(replace(previous, stop=stop))
        else:
            selected.append(Slot(start, stop, winner.title, winner.description, winner.category,
                                 winner.icon, winner.priority, winner.granularity))
    granularity = "series-and-block" if any(slot.granularity == "series" for slot in selected) else "block"
    root = ET.Element("tv", {"source-info-url": FOXKIDS_PAGE, "data-granularity": granularity})
    channel = ET.SubElement(root, "channel", {"id": FOXKIDS_ID})
    ET.SubElement(channel, "display-name", {"lang": "pt"}).text = "Fox Kids"
    for slot in selected:
        _append_programme(root, FOXKIDS_ID, slot)
    if not has_coverage(root, FOXKIDS_ID, now):
        raise ValueError("Declared Fox Kids schedule lacks current and future coverage")
    return root


def _append_programme(root: ET.Element, channel_id: str, slot: Slot) -> None:
    programme = ET.SubElement(root, "programme", {"channel": channel_id,
        "start": slot.start.strftime("%Y%m%d%H%M%S %z"),
        "stop": slot.stop.strftime("%Y%m%d%H%M%S %z")})
    ET.SubElement(programme, "title", {"lang": "pt"}).text = slot.title
    if slot.description:
        ET.SubElement(programme, "desc", {"lang": "pt"}).text = slot.description
    if slot.category:
        ET.SubElement(programme, "category", {"lang": "pt"}).text = slot.category
    if slot.icon:
        ET.SubElement(programme, "icon", {"src": slot.icon})


def fetch_foxkids(client: HttpClient, url: str = FOXKIDS_CATALOG, *, cache_dir: Path | None = None,
                  now: datetime | None = None, days: int = 3) -> ET.Element:
    now = now or datetime.now(timezone.utc)
    try:
        direct = cached_payload(client, FOXKIDS_XMLTV, cache_dir=cache_dir, ttl_seconds=3600, now=now)
        root = _fox_xmltv(direct, now)
        root.set("data-origin", "published-xmltv")
        return root
    except (ValueError, RuntimeError, ET.ParseError, OSError):
        logger.info("Fox Kids published XMLTV unavailable or expired; checking declared schedule")
    payload = cached_payload(client, url, cache_dir=cache_dir, ttl_seconds=3600, now=now)
    root = foxkids_catalog_to_xmltv(payload, now=now, days=days)
    root.set("data-origin", "declared-catalog")
    return root


class _Document(HTMLParser):
    """Small DOM for schedule markup; scripts remain inert text."""
    VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "area", "base", "embed"}

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("document")
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = ET.SubElement(self.stack[-1], tag, {key: value or "" for key, value in attrs})
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        node = ET.SubElement(self.stack[-1], "text")
        node.text = data


def _text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _aware_iso(value: str, zone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed


def parse_guiadetv_html(payload: bytes, *, zone: ZoneInfo = SCHEDULE_ZONE) -> list[Slot]:
    """Use the BroadcastEvent JSON-LD actually published inside channel HTML."""
    document = _Document(payload.decode("utf-8")).root
    result = []

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if types == "BroadcastEvent" or isinstance(types, list) and "BroadcastEvent" in types:
                try:
                    start = _aware_iso(value["startDate"], zone)
                    stop = _aware_iso(value["endDate"], zone)
                    title = str(value["name"]).strip()
                    if title and stop > start:
                        result.append(Slot(start, stop, title, str(value.get("description") or "")))
                except (KeyError, TypeError, ValueError):
                    pass
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    for script in document.iter("script"):
        if script.get("type", "").casefold() == "application/ld+json":
            try:
                visit(json.loads("".join(script.itertext())))
            except (ValueError, TypeError):
                continue
    return sorted(set(result), key=lambda slot: slot.start)


def parse_meuguia_html(payload: bytes, *, now: datetime, zone: ZoneInfo = SCHEDULE_ZONE) -> list[Slot]:
    """Keep explicit days/start times; infer stop only from the next programme."""
    document = _Document(payload.decode("utf-8")).root
    today = now.astimezone(zone).date()
    day = None
    previous = None
    records = []
    for row in document.iter("li"):
        if "subheader" in row.get("class", "").split():
            match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", _text(row))
            if match:
                day_number, month, year = match.groups()
                if year:
                    day = date(int(year), int(month), int(day_number))
                else:
                    choices = []
                    for candidate_year in (today.year - 1, today.year, today.year + 1):
                        try:
                            choices.append(date(candidate_year, int(month), int(day_number)))
                        except ValueError:
                            continue
                    if not choices:
                        continue
                    day = min(choices, key=lambda candidate: abs((candidate - today).days))
                previous = None
            continue
        clock_node = next((item for item in row.iter("div") if {"lileft", "time"} <= set(item.get("class", "").split())), None)
        title = _text(row.find(".//h2"))
        clock = _text(clock_node)
        if day is None or not title or not re.fullmatch(r"\d{2}:\d{2}", clock):
            continue
        hour, minute = map(int, clock.split(":"))
        try:
            start = datetime.combine(day, day_time(hour, minute), zone)
        except ValueError:
            continue
        if previous and start < previous:
            day += timedelta(days=1)
            start = datetime.combine(day, day_time(hour, minute), zone)
        previous = start
        records.append((start, title, _text(row.find(".//h3"))))
    # A page can repeat its own rows; duplicate starts are not separate shows.
    unique = sorted({record[0]: record for record in records}.values())
    return [Slot(start, following[0], title, category=category)
            for (start, title, category), following in zip(unique, unique[1:])
            if timedelta(0) < following[0] - start <= timedelta(hours=12)]


def fetch_html_schedules(client: HttpClient, manifest_url: str, source: str, *,
                         cache_dir: Path | None = None, now: datetime | None = None) -> ET.Element:
    """Opt-in explicit channel manifest; bounded sequential collection.

    The manifest maps canonical IDs and region-specific page URLs. No broad
    channel discovery or source-owned alias table is imported automatically.
    """
    from urllib.parse import urlsplit

    now = now or datetime.now(timezone.utc)
    payload = cached_payload(client, manifest_url, cache_dir=cache_dir, ttl_seconds=3600, now=now)
    manifest = json.loads(payload)
    zone = ZoneInfo(manifest.get("timezone", "America/Sao_Paulo"))
    channels = manifest.get("channels")
    if not isinstance(channels, list) or not 1 <= len(channels) <= 32:
        raise ValueError("HTML EPG manifest requires between one and 32 channels")
    allowed = {"guiadetv": {"guiadetv.com", "www.guiadetv.com"}, "meuguia": {"meuguia.tv", "www.meuguia.tv"}}
    if source not in allowed:
        raise ValueError("Unknown HTML EPG source")
    root = ET.Element("tv", {"data-granularity": "programme"})
    failures = []
    seen = set()
    for index, item in enumerate(channels):
        channel_id = str(item.get("id", "")).strip()
        url = str(item.get("url", ""))
        parsed = urlsplit(url)
        if not channel_id or channel_id in seen or parsed.scheme != "https" or parsed.hostname not in allowed[source]:
            raise ValueError("HTML EPG manifest has an invalid channel ID or source URL")
        seen.add(channel_id)
        if index:
            time.sleep(1)
        try:
            data = cached_payload(client, url, cache_dir=cache_dir, ttl_seconds=1800, now=now)
            slots = parse_guiadetv_html(data, zone=zone) if source == "guiadetv" else parse_meuguia_html(data, now=now, zone=zone)
            single = ET.Element("tv")
            channel = ET.SubElement(single, "channel", {"id": channel_id})
            ET.SubElement(channel, "display-name").text = str(item.get("name") or channel_id)
            for slot in slots:
                if slot.stop > now and slot.start < now + timedelta(days=3):
                    _append_programme(single, channel_id, slot)
            if not has_coverage(single, channel_id, now):
                raise ValueError("Channel schedule has no current and future coverage")
            root.extend(single)
        except Exception as exc:
            logger.warning("HTML EPG channel failed id=%s error_type=%s", channel_id, type(exc).__name__)
            failures.append(channel_id)
    if not root.findall("programme"):
        raise ValueError("No HTML EPG channel has current and future coverage")
    root.set("data-failed-channels", json.dumps(failures))
    return root
