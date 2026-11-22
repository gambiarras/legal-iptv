from legal_iptv.local_config import environment_value, load_resource, runtime_value

"""Public schedule adapters. No playback URL or downloaded code is evaluated."""

import copy
import io
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from datetime import time as day_time
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from legal_iptv.clients import HttpClient

logger = logging.getLogger(__name__)
CHANNEL_SCHEDULE_ID = runtime_value('channel_schedule_id', 'RequiredChannel.br')
CHANNEL_SCHEDULE_PAGE = runtime_value('channel_schedule_page', 'https://channel-schedule-page.example.invalid/')
CHANNEL_SCHEDULE_XMLTV = runtime_value('channel_schedule_xmltv', 'https://channel-schedule-xmltv.example.invalid/')
CHANNEL_SCHEDULE_CATALOG = runtime_value('channel_schedule_catalog', 'https://channel-schedule-catalog.example.invalid/')
SCHEDULE_ZONE = ZoneInfo("America/Sao_Paulo")
WEEKDAYS = {name: index for index, name in enumerate(("seg", "ter", "qua", "qui", "sex", "sab", "dom"))}
MAX_PAYLOAD = 25 * 1024 * 1024


def cached_payload(client: HttpClient, url: str, *, cache_dir: Path | None,
                   ttl_seconds: int, now: datetime, headers: dict | None = None,
                   lkg_seconds: int = 0, metadata: dict | None = None) -> bytes:
    from legal_iptv.services.epg_cache import cached_source

    with cached_source(client, url, cache_dir=cache_dir, ttl_seconds=ttl_seconds,
                       now=now, headers=headers, max_bytes=MAX_PAYLOAD,
                       lkg_seconds=lkg_seconds) as source:
        if metadata is not None:
            metadata.update(origin=source.origin, fetched_at=source.fetched_at.isoformat())
        return source.path.read_bytes()


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
    root = ET.Element("tv", {"source-info-url": runtime_value("channel_schedule_page", CHANNEL_SCHEDULE_PAGE), "data-granularity": "programme"})
    upstream = None
    depth = 0
    for event, element in ET.iterparse(io.BytesIO(payload), events=("start", "end")):
        if event == "start":
            depth += 1
            if upstream is None:
                upstream = element
                if element.tag != "tv":
                    raise ValueError("Required Channel source is not XMLTV")
            continue
        if depth == 2:
            attribute = "id" if element.tag == "channel" else "channel"
            if element.tag in {"channel", "programme"} and element.get(attribute, "").casefold() in set(runtime_value("channel_schedule_aliases", [runtime_value("channel_schedule_id", CHANNEL_SCHEDULE_ID).casefold()])):
                valid = True
                if element.tag == "programme":
                    start = _xmltv_time(element.get("start", ""))
                    stop = _xmltv_time(element.get("stop", ""))
                    valid = stop > start and stop > now
                if valid:
                    copied = copy.deepcopy(element)
                    copied.set(attribute, runtime_value("channel_schedule_id", CHANNEL_SCHEDULE_ID))
                    root.append(copied)
            element.clear()
            upstream.remove(element)
        depth -= 1
    if not any(channel.get("id") == runtime_value("channel_schedule_id", CHANNEL_SCHEDULE_ID) for channel in root.findall("channel")) or not has_coverage(root, runtime_value("channel_schedule_id", CHANNEL_SCHEDULE_ID), now):
        raise ValueError("Published Required Channel XMLTV lacks current and future coverage")
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
            raise ValueError("Required Channel schedule has an invalid clock")
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
            raise ValueError("Required Channel catalog has a cyclic mirror")
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
            raise ValueError("Required Channel schedule lacks an explicit duration")
        duration = int(length.text or "0")
        if not 0 < duration <= 48 * 60:
            raise ValueError("Required Channel schedule duration is outside the supported window")
        cycles = []
        for block in dated or [item]:
            start = block.get("start", item.get("start", ""))
            repeats = int(block.get("repeat", item.get("repeat", "1")))
            if not 1 <= repeats <= 1000:
                raise ValueError("Required Channel schedule repeat count is invalid")
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
        raise ValueError("Required Channel recurring schedule exceeds the supported history")
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


def channel_schedule_catalog_to_xmltv(payload: bytes, *, now: datetime, days: int = 3) -> ET.Element:
    """Expand only dates/times declared by the site's catalog.

    Blocks retain their source titles. Episode sequencing is deliberately not
    inferred. Dated films and finite special events may override a block; no
    missing interval is filled, and no old XMLTV date is shifted.
    """
    catalog = ET.fromstring(payload)
    if catalog.tag != "catalog":
        raise ValueError("Required Channel source is not a schedule catalog")
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
    root = ET.Element("tv", {"source-info-url": runtime_value("channel_schedule_page", CHANNEL_SCHEDULE_PAGE), "data-granularity": granularity})
    channel = ET.SubElement(root, "channel", {"id": runtime_value("channel_schedule_id", CHANNEL_SCHEDULE_ID)})
    ET.SubElement(channel, "display-name", {"lang": "pt"}).text = runtime_value("channel_schedule_name", "Required Channel")
    for slot in selected:
        _append_programme(root, runtime_value("channel_schedule_id", CHANNEL_SCHEDULE_ID), slot)
    if not has_coverage(root, runtime_value("channel_schedule_id", CHANNEL_SCHEDULE_ID), now):
        raise ValueError("Declared Required Channel schedule lacks current and future coverage")
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


def fetch_channel_schedule(client: HttpClient, url: str | None = None, *, cache_dir: Path | None = None,
                  now: datetime | None = None, days: int = 3) -> ET.Element:
    now = now or datetime.now(timezone.utc)
    url = url or runtime_value("channel_schedule_catalog", CHANNEL_SCHEDULE_CATALOG)
    try:
        direct = cached_payload(client, runtime_value("channel_schedule_xmltv", CHANNEL_SCHEDULE_XMLTV), cache_dir=cache_dir, ttl_seconds=3600, now=now)
        root = _fox_xmltv(direct, now)
        root.set("data-origin", "published-xmltv")
        return root
    except (ValueError, RuntimeError, ET.ParseError, OSError):
        logger.info("Required Channel published XMLTV unavailable or expired; checking declared schedule")
    payload = cached_payload(client, url, cache_dir=cache_dir, ttl_seconds=3600, now=now)
    root = channel_schedule_catalog_to_xmltv(payload, now=now, days=days)
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


def parse_schedule_site_a_html(payload: bytes, *, zone: ZoneInfo = SCHEDULE_ZONE) -> list[Slot]:
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


def parse_schedule_site_b_html(payload: bytes, *, now: datetime, zone: ZoneInfo = SCHEDULE_ZONE) -> list[Slot]:
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
                         cache_dir: Path | None = None, now: datetime | None = None,
                         channel_ids: set[str] | None = None) -> ET.Element:
    """Opt-in explicit channel manifest; bounded sequential collection.

    The manifest maps canonical IDs and region-specific page URLs. No broad
    channel discovery or source-owned alias table is imported automatically.
    """
    now = now or datetime.now(timezone.utc)
    payload = cached_payload(client, manifest_url, cache_dir=cache_dir, ttl_seconds=3600, now=now)
    manifest = json.loads(payload)
    return _collect_html_manifest(client, manifest, source, cache_dir=cache_dir, now=now,
                                  channel_ids=channel_ids)


def _collect_html_manifest(client, manifest, source, *, cache_dir, now, channel_ids=None):
    from urllib.parse import urlsplit

    zone = ZoneInfo(manifest.get("timezone", "America/Sao_Paulo"))
    channels = manifest.get("channels")
    if not isinstance(channels, list) or not 1 <= len(channels) <= 32:
        raise ValueError("HTML EPG manifest requires between one and 32 channels")
    allowed = runtime_value("schedule_allowed_hosts", {})
    if source not in allowed:
        raise ValueError("Unknown HTML EPG source")
    root = ET.Element("tv", {"data-granularity": "programme"})
    failures = []
    partial = []
    seen = set()
    channels = [item for item in channels if channel_ids is None or item.get("id") in channel_ids]
    previous_network = False
    for index, item in enumerate(channels):
        channel_id = str(item.get("id", "")).strip()
        url = str(item.get("url", ""))
        parsed = urlsplit(url)
        if not channel_id or channel_id in seen or parsed.scheme != "https" or parsed.hostname not in allowed[source]:
            raise ValueError("HTML EPG manifest has an invalid channel ID or source URL")
        seen.add(channel_id)
        if index and previous_network:
            time.sleep(1)
        try:
            previous_network = True
            metadata = {}
            data = cached_payload(client, url, cache_dir=cache_dir, ttl_seconds=1800, now=now,
                                  lkg_seconds=21600, metadata=metadata)
            previous_network = metadata.get("origin") == "network"
            slots = parse_schedule_site_a_html(data, zone=zone) if source == "schedule_site_a" else parse_schedule_site_b_html(data, now=now, zone=zone)
            single = ET.Element("tv")
            channel = ET.SubElement(single, "channel", {"id": channel_id})
            ET.SubElement(channel, "display-name").text = str(item.get("name") or channel_id)
            for slot in slots:
                if slot.stop > now and slot.start < now + timedelta(days=3):
                    _append_programme(single, channel_id, slot)
            if not single.findall("programme"):
                raise ValueError("Channel schedule has no dated programmes in the window")
            if not has_coverage(single, channel_id, now):
                partial.append(channel_id)
            root.extend(single)
        except Exception as exc:
            logger.warning("HTML EPG channel failed id=%s error_type=%s", channel_id, type(exc).__name__)
            failures.append(channel_id)
    if channels and not root.findall("programme"):
        raise ValueError("No HTML EPG channel has dated programmes in the window")
    root.set("data-failed-channels", json.dumps(failures))
    root.set("data-partial-channels", json.dumps(partial))
    return root


def parse_official_schedule_a_html(payload: bytes, day: date, *, zone: ZoneInfo = SCHEDULE_ZONE) -> list[Slot]:
    """Read dated HTML rows and inert JSON metadata, never evaluate onclick."""
    document = _Document(payload.decode("utf-8")).root
    slots = []
    for row in document.iter("div"):
        if "group" not in row.get("class", "").split():
            continue
        clocks = [_text(item) for item in row.iter("span")
                  if re.fullmatch(r"\d{2}:\d{2}", _text(item))]
        title = _text(row.find(".//h3"))
        if len(clocks) != 2 or not title:
            continue
        metadata = {}
        for button in row.iter("button"):
            match = re.fullmatch(r"\s*abrirDetalhes\((\{.*\})\)\s*;?\s*", button.get("onclick", ""), re.DOTALL)
            if match:
                try:
                    value = json.loads(match.group(1))
                    if isinstance(value, dict):
                        metadata = value
                except ValueError:
                    pass
        if metadata.get("tipo") == "event" and metadata.get("evt_titulo"):
            title = str(metadata["evt_titulo"])
        start = datetime.combine(day, day_time.fromisoformat(clocks[0]), zone)
        stop = datetime.combine(day, day_time.fromisoformat(clocks[1]), zone)
        if stop < start:
            stop += timedelta(days=1)
        if stop <= start or stop - start > timedelta(days=1):
            continue
        # A programme title and its declared interval are sufficient; film
        # descriptions/season/episode are not guessed from session labels.
        slots.append(Slot(start, stop, title, category=str(metadata.get("prog_formato") or "")))
    return sorted(set(slots), key=lambda slot: slot.start)


def parse_official_schedule_b_html(payload: bytes, *, now: datetime, zone: ZoneInfo = SCHEDULE_ZONE) -> list[Slot]:
    """Only explicit dated structured events are usable; an empty week stays empty."""
    text = payload.decode("iso-8859-1")
    # Accept future factual BroadcastEvent records should the publisher add
    # them, rather than extrapolating a weekly grid without validity dates.
    return parse_schedule_site_a_html(text.encode("utf-8"), zone=zone)


def fetch_official_schedule(client: HttpClient, url: str, source: str, *,
                            cache_dir: Path | None = None, now: datetime | None = None,
                            days: int = 3, channel_ids=None) -> ET.Element:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    if source not in {"official_schedule_a", "official_schedule_b"}:
        raise ValueError("Unknown official EPG source")
    now = now or datetime.now(timezone.utc)
    channel_id = runtime_value("official_schedules", {}).get(source, {}).get("id", "ExampleStation.br")
    title = runtime_value("official_schedules", {}).get(source, {}).get("name", "Example Station")
    root = ET.Element("tv", {"data-origin": "official-html", "data-granularity": "programme"})
    if channel_ids is not None and channel_id not in channel_ids:
        return root
    zone_name = environment_value("IPTV_EPG_OFFICIAL_SCHEDULE_A_TIMEZONE" if source == "official_schedule_a" else "IPTV_EPG_OFFICIAL_SCHEDULE_B_TIMEZONE", "America/Sao_Paulo")
    zone = ZoneInfo(zone_name)
    slots = []
    failed_dates = []
    observations = []
    if source == "official_schedule_a":
        for offset in range(max(1, min(days, 3))):
            day = now.astimezone(zone).date() + timedelta(days=offset)
            parsed = urlsplit(url)
            query = dict(parse_qsl(parsed.query))
            query["data"] = day.isoformat()
            endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
            observation = {}
            try:
                payload = cached_payload(client, endpoint, cache_dir=cache_dir, ttl_seconds=900,
                                         lkg_seconds=21600, now=now, metadata=observation)
                slots.extend(parse_official_schedule_a_html(payload, day, zone=zone))
                observations.append(observation)
            except (OSError, RuntimeError, ValueError):
                failed_dates.append(day.isoformat())
            if offset + 1 < max(1, min(days, 3)):
                time.sleep(0.5)
    else:
        observation = {}
        payload = cached_payload(client, url, cache_dir=cache_dir, ttl_seconds=1800,
                                 lkg_seconds=21600, now=now, metadata=observation)
        slots = parse_official_schedule_b_html(payload, now=now, zone=zone)
        observations.append(observation)
    channel = ET.SubElement(root, "channel", {"id": channel_id})
    ET.SubElement(channel, "display-name").text = title
    for slot in slots:
        if slot.stop > now and slot.start < now + timedelta(days=max(1, min(days, 3))):
            _append_programme(root, channel_id, slot)
    root.set("data-failed-dates", json.dumps(failed_dates))
    root.set("data-schedule-state", "partial" if root.findall("programme") else "empty")
    root.set("data-timezone", zone_name)
    if observations:
        root.set("data-collected-at", min(item["fetched_at"] for item in observations))
        root.set("data-cache-origin", "lkg" if any(item["origin"] == "lkg" for item in observations) else "dated-source")
    return root


def fetch_mapped_schedules(client: HttpClient, source: str, *, cache_dir: Path | None = None,
                           now: datetime | None = None, channel_ids=None,
                           channel_names: dict[str, str] | None = None) -> ET.Element:
    import unicodedata

    if source not in {"schedule_site_a", "schedule_site_b"}:
        raise ValueError("Unknown mapped HTML EPG source")
    now = now or datetime.now(timezone.utc)
    if channel_ids is None:
        # A broad grab of the resource map is never the default.
        return ET.Element("tv", {"data-schedule-state": "selection-required"})
    mappings = load_resource("epg_site_mappings.json")["channels"]

    def normalise(value):
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
        return " ".join(re.findall(r"[a-z0-9]+", value))

    rows = []
    selected_names = channel_names or {}
    for identifier in sorted(set(channel_ids)):
        name = selected_names.get(identifier)
        matches = [item for item in mappings if identifier in item.get("ids", []) or
                   name is not None and normalise(name) in {normalise(alias) for alias in item["names"]}]
        codes = {item[source] for item in matches if item.get(source)}
        if len(codes) != 1:
            continue
        code = next(iter(codes))
        endpoint = runtime_value("schedule_url_templates", {})[source].format(code=code)
        rows.append({"id": identifier, "name": name or matches[0]["names"][0], "url": endpoint})
    maximum = max(1, min(int(environment_value("IPTV_EPG_HTML_MAX_CHANNELS", "32")), 128))
    chosen = rows[:maximum]
    if not chosen:
        return ET.Element("tv", {"data-schedule-state": "no-mapped-selected-channels"})
    # Split the bounded selected set into old-compatible batches of at most 32.
    roots = []
    for index in range(0, len(chosen), 32):
        batch = {"timezone": "America/Sao_Paulo", "channels": chosen[index:index + 32]}
        roots.append(_collect_html_manifest(client, batch, source, cache_dir=cache_dir, now=now,
                                           channel_ids=set(channel_ids)))
    root = ET.Element("tv", {"data-origin": f"selected-{source}", "data-granularity": "programme"})
    for item in roots:
        root.extend(item)
    root.set("data-skipped-channels", json.dumps([item["id"] for item in rows[maximum:]]))
    return root
