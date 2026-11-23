"""Bounded text-feed ingestion restricted to explicit operator-reviewed entries."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from legal_iptv.clients import HttpClient
from legal_iptv.clients.http_client import build_headers
from legal_iptv.io import write_json_atomic
from legal_iptv.local_config import environment_value, runtime_value
from legal_iptv.models import Channel
from legal_iptv.services.public_media_transport import (
    checked_https,
    is_public_media_transport,
)

MAX_SOURCES = 8
MAX_ENTRIES = 100
MAX_BYTES = 2 * 1024 * 1024
TTL_SECONDS = 1800
LKG_SECONDS = 21600
FIELDS = {"canal", "fonte", "logo", "categoria"}


@dataclass(frozen=True, slots=True)
class CuratedEntry:
    match_name: str
    id: str
    name: str
    tvg_id: str
    group: str
    media_host: str
    media_path: str


@dataclass(frozen=True, slots=True)
class CuratedSource:
    id: str
    url: str
    entries: tuple[CuratedEntry, ...]
    fingerprint: str


class CuratedTextUnavailable(RuntimeError):
    """A required configured source has neither complete discovery nor valid LKG."""


def load_sources() -> tuple[CuratedSource, ...]:
    values = runtime_value("curated_text_sources", [])
    if not isinstance(values, list) or len(values) > MAX_SOURCES:
        raise ValueError("Invalid curated text source inventory")
    sources = []
    ids = set()
    channel_ids = set()
    for value in values:
        if not isinstance(value, dict):
            raise TypeError("Invalid curated text source configuration")
        identifier = value.get("id")
        url = environment_value(str(value.get("url_env") or ""), None) or value.get(
            "url"
        )
        rows = value.get("entries")
        parsed = checked_https(url) if isinstance(url, str) else None
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier) is None
            or identifier in ids
            or not isinstance(url, str)
            or parsed is None
            or parsed.query
            or not isinstance(rows, list)
            or not 1 <= len(rows) <= MAX_ENTRIES
        ):
            raise ValueError("Invalid curated text source configuration")
        entries = []
        names = set()
        for row in rows:
            if not isinstance(row, dict) or any(
                not isinstance(row.get(key), str)
                or not row[key].strip()
                or len(row[key]) > 4096
                or any(ord(char) < 32 for char in row[key])
                for key in CuratedEntry.__dataclass_fields__
            ):
                raise ValueError("Invalid curated text allowlist entry")
            entry = CuratedEntry(
                **{key: row[key] for key in CuratedEntry.__dataclass_fields__}
            )
            media = checked_https("https://" + entry.media_host + entry.media_path)
            if (
                entry.match_name in names
                or entry.id in channel_ids
                or media is None
                or media.hostname != entry.media_host
                or media.path != entry.media_path
                or media.query
                or not entry.media_path.endswith(".m3u8")
            ):
                raise ValueError("Invalid curated text allowlist entry")
            names.add(entry.match_name)
            channel_ids.add(entry.id)
            entries.append(entry)
        fingerprint = hashlib.sha256(
            json.dumps(
                {**value, "url": url}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        sources.append(CuratedSource(identifier, url, tuple(entries), fingerprint))
        ids.add(identifier)
    return tuple(sources)


def _channel(
    entry: CuratedEntry, fields: Mapping[str, str], source: CuratedSource
) -> Channel:
    media = checked_https(fields.get("fonte", ""))
    if (
        media is None
        or media.hostname != entry.media_host
        or media.path != entry.media_path
        or not {"canal", "fonte", "logo", "categoria"}.issubset(fields)
    ):
        raise ValueError("Incomplete or unreviewed curated text media")
    channel = Channel(
        id=entry.id,
        name=entry.name,
        stream_url=fields["fonte"],
        logo=fields.get("logo", ""),
        group=entry.group,
        raw_group=entry.group,
        source="extra",
        source_type="curated_text",
        provider_id="extra",
        source_url=source.url,
        tvg_id=entry.tvg_id,
        logical_channel_id=entry.id,
        variant_id=entry.id,
        protocol="hls",
        status="resolved",
    )
    logo = checked_https(channel.logo) if channel.logo else None
    if channel.logo and (logo is None or logo.query):
        # Presentation never widens the media exception or blocks approved playback.
        channel.logo = ""
    if not is_public_media_transport(channel):
        raise ValueError("Curated text media does not match its public transport rule")
    return channel


def parse_source(text: str, source: CuratedSource) -> list[Channel]:
    """Scan the bounded feed without retaining entries outside the exact allowlist."""
    allowlist = {entry.match_name: entry for entry in source.entries}
    found: dict[str, Channel] = {}
    fields: dict[str, str] = {}
    current: CuratedEntry | None = None

    def flush() -> None:
        if current is not None:
            if current.id in found:
                raise ValueError("Duplicate curated text channel")
            found[current.id] = _channel(current, fields, source)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            if current is not None:
                raise ValueError("Malformed curated text entry")
            continue
        key, value = key.strip().casefold(), value.strip()
        if key == "canal":
            flush()
            current = allowlist.get(value)
            fields = {"canal": value} if current is not None else {}
        elif current is not None and key in FIELDS:
            if key in fields:
                raise ValueError("Duplicate curated text field")
            fields[key] = value
    flush()
    if len(found) != len(source.entries):
        raise ValueError("Curated text source is incomplete")
    return [found[entry.id] for entry in source.entries]


def _download(client: HttpClient, url: str) -> str:
    """Bound source downloads by bytes, total elapsed time and each blocking read."""
    budget = max(0.1, float(client.timeout))
    deadline = time.monotonic() + budget
    headers = build_headers()
    headers["Accept-Encoding"] = "identity"
    response = client.session.get(
        url,
        headers=headers,
        timeout=(min(3.0, budget), min(3.0, budget)),
        stream=True,
        allow_redirects=False,
    )
    try:
        if response.status_code != 200:
            raise ValueError("Curated text source request failed")
        raw = response.raw
        if raw is None or not callable(getattr(raw, "read1", None)):
            raise ValueError("Curated text source cannot provide bounded streaming")
        content = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("Curated text source download exceeded its deadline")
            chunk = raw.read1(
                min(4096, MAX_BYTES + 1 - len(content)), decode_content=True
            )
            if time.monotonic() >= deadline:
                raise ValueError("Curated text source download exceeded its deadline")
            if not chunk:
                break
            if len(content) + len(chunk) > MAX_BYTES:
                raise ValueError("Curated text source exceeds its size limit")
            content.extend(chunk)
        return content.decode("utf-8-sig")
    except (Urllib3HTTPError, OSError):
        # Raw streaming errors are not always wrapped by requests. Preserve LKG
        # through the same source-specific failure path instead of losing extras.
        raise requests.RequestException(
            "Curated text source streaming failed"
        ) from None
    finally:
        response.close()


def _cached(
    path: Path, source: CuratedSource, now: datetime
) -> tuple[list[Channel], str, float] | None:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or data.get("fingerprint") != source.fingerprint:
            return None
        observed = datetime.fromisoformat(data["observed_at"])
        age = (now - observed).total_seconds()
        if observed.tzinfo is None or not 0 <= age <= LKG_SECONDS:
            return None
        rows = data["entries"]
        if not isinstance(rows, dict) or set(rows) != {
            entry.id for entry in source.entries
        }:
            return None
        channels = [_channel(entry, rows[entry.id], source) for entry in source.entries]
        return channels, observed.isoformat(), age
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _prune(cache_dir: Path, sources: tuple[CuratedSource, ...], now: datetime) -> None:
    """Keep only the current bounded source inventory; ignore unrelated cache files."""
    active = {source.id for source in sources}
    if not cache_dir.is_dir():
        return
    for path in cache_dir.glob("*.json"):
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", path.stem) is None:
            continue
        try:
            if path.is_symlink():
                continue
            size = path.stat().st_size
            if size > MAX_BYTES:
                # Ownership cannot be established safely within the read bound.
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            owned = (
                isinstance(data, dict)
                and data.get("version") == 1
                and "fingerprint" in data
                and "entries" in data
            )
            if owned and (
                path.stem not in active
                or (now - datetime.fromisoformat(data["observed_at"])).total_seconds()
                > LKG_SECONDS
            ):
                path.unlink()
        except (OSError, ValueError, TypeError, KeyError):
            continue


def fetch_channels(
    client: HttpClient | None,
    *,
    cache_dir: Path,
    cache_only: bool = False,
    now: datetime | None = None,
) -> list[Channel]:
    try:
        sources = load_sources()
    except (ValueError, TypeError, KeyError):
        raise CuratedTextUnavailable(
            "Invalid curated text source configuration"
        ) from None
    if not sources:
        return []
    fixed_now = now
    now = now or datetime.now(UTC)
    channels = []
    _prune(cache_dir, sources, now)
    for source in sources:
        path = cache_dir / (source.id + ".json")
        cached = _cached(path, source, now)
        if cached is not None and (cache_only or cached[2] <= TTL_SECONDS):
            channels.extend(cached[0])
            continue
        if cache_only or client is None:
            raise CuratedTextUnavailable("Curated text source cache is unavailable")
        try:
            selected = parse_source(_download(client, source.url), source)
        except (
            requests.RequestException,
            Urllib3HTTPError,
            OSError,
            ValueError,
            TypeError,
            UnicodeError,
        ):
            retained = _cached(path, source, fixed_now or datetime.now(UTC))
            if retained is not None:
                channels.extend(retained[0])
                continue
            raise CuratedTextUnavailable(
                "Curated text source has no complete usable snapshot"
            ) from None
        entries: dict[str, dict[str, str]] = {
            entry.id: {
                "canal": entry.match_name,
                "fonte": channel.stream_url,
                "logo": channel.logo,
                "categoria": entry.group,
            }
            for entry, channel in zip(source.entries, selected)
        }
        payload = {
            "version": 1,
            "fingerprint": source.fingerprint,
            "observed_at": now.isoformat(),
            "entries": entries,
        }
        if len(json.dumps(payload).encode("utf-8")) > MAX_BYTES:
            raise CuratedTextUnavailable("Curated text snapshot exceeds its size limit")
        try:
            cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_json_atomic(path, payload)
            path.chmod(0o600)
        except OSError:
            raise CuratedTextUnavailable(
                "Curated text snapshot could not be persisted"
            ) from None
        channels.extend(selected)
    return channels
