"""Bounded, private EPG source cache and streaming downloads."""

import hashlib
import json
import logging
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlsplit

import requests

from legal_iptv.clients import HttpClient
from legal_iptv.clients.http_client import build_headers

logger = logging.getLogger(__name__)
CACHE_MAX_BYTES = 256 * 1024 * 1024
CACHE_MAX_FILES = 96
CACHE_MAX_AGE_SECONDS = 48 * 3600
UPSTREAM_MAX_BYTES = 128 * 1024 * 1024


def _budget(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _headroom(path: Path, incoming: int = 0) -> None:
    required = _budget("IPTV_EPG_MIN_FREE_BYTES", 200 * 1024 * 1024)
    if shutil.disk_usage(path).free - incoming < required:
        raise OSError("EPG cache has insufficient free disk headroom")


@dataclass(frozen=True)
class CachedSource:
    path: Path
    origin: str
    fetched_at: datetime


def prune_cache(cache_dir: Path | None, *, now: datetime,
                max_bytes: int = CACHE_MAX_BYTES, max_files: int = CACHE_MAX_FILES,
                max_age_seconds: int = CACHE_MAX_AGE_SECONDS,
                protected: tuple[Path, ...] = ()) -> None:
    """Prune this cache's own files; no recursion or unrelated file deletion."""
    if cache_dir is None or not cache_dir.is_dir():
        return
    max_bytes = _budget("IPTV_EPG_CACHE_MAX_BYTES", max_bytes)
    max_files = _budget("IPTV_EPG_CACHE_MAX_FILES", max_files)
    max_age_seconds = _budget("IPTV_EPG_CACHE_MAX_AGE_SECONDS", max_age_seconds)
    entries = []
    for path in cache_dir.iterdir():
        owned = (re.fullmatch(r"[a-f0-9]{64}", path.stem) is not None and path.suffix == ".data") or path.name in {"channel-inventory.json", "guide-selection.json"}
        owned |= path.name.startswith(("epg-", ".write-")) and path.suffix == ".tmp"
        if not owned or not path.is_file() or path.is_symlink():
            continue
        try:
            stat = path.stat()
            age = now.timestamp() - stat.st_mtime
            stale_temp = path.suffix == ".tmp" and age > 3600
            if path not in protected and (age > max_age_seconds or stale_temp or age < -300):
                path.unlink(missing_ok=True)
            else:
                entries.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            logger.warning("EPG cache entry could not be inspected or pruned")
    size = sum(item[1] for item in entries)
    count = len(entries)
    for _, length, path in sorted(entries):
        if size <= max_bytes and count <= max_files:
            break
        if path in protected:
            continue
        try:
            path.unlink(missing_ok=True)
            size -= length
            count -= 1
        except OSError:
            logger.warning("EPG cache budget could not remove an entry")


def _cache_key(url: str, headers: dict | None) -> str:
    identity = url if not headers else json.dumps([url, sorted(headers.items())], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def _download(client: HttpClient, url: str, temporary: Path, *,
              max_bytes: int, headers: dict | None, cache_dir: Path | None,
              protected: tuple[Path, ...], now: datetime) -> None:
    """Production HttpClient streams bytes; tiny fake clients keep tests simple."""
    session = getattr(client, "session", None)
    if not isinstance(client, HttpClient) or not isinstance(session, requests.Session):
        payload = client.get_bytes(url, headers=headers)
        if not payload or len(payload) > max_bytes:
            raise ValueError("EPG payload is empty or exceeds its byte budget")
        _headroom(temporary.parent, len(payload))
        temporary.write_bytes(payload)
        return
    request_headers = build_headers(accept="application/xml,text/html,application/json,*/*")
    request_headers.update(headers or {})
    last_error = None
    for attempt in range(max(1, min(client.retries, 3))):
        current = url
        effective_headers = dict(request_headers)
        try:
            for redirect in range(6):
                response = session.get(current, headers=effective_headers, timeout=client.timeout,
                                       stream=True, allow_redirects=False)
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location or redirect == 5:
                            raise ValueError("EPG redirect chain is invalid or too long")
                        destination = urljoin(current, location)
                        before, after = urlsplit(current), urlsplit(destination)
                        if after.scheme not in {"http", "https"}:
                            raise ValueError("EPG redirect has an unsupported scheme")
                        if (before.scheme, before.netloc) != (after.scheme, after.netloc):
                            effective_headers = {key: value for key, value in effective_headers.items()
                                                 if key.casefold() in {"user-agent", "accept", "accept-language", "accept-encoding"}}
                        current = destination
                        continue
                    response.raise_for_status()
                    length = 0
                    next_budget_check = 0
                    with temporary.open("wb") as output:
                        for chunk in response.iter_content(64 * 1024):
                            length += len(chunk)
                            if length > max_bytes:
                                raise ValueError("EPG payload exceeds its byte budget")
                            if length >= next_budget_check:
                                _headroom(temporary.parent, len(chunk))
                                prune_cache(cache_dir, now=now, protected=protected)
                                next_budget_check = length + 4 * 1024 * 1024
                            output.write(chunk)
                    if not length:
                        raise ValueError("EPG payload is empty")
                    return
                finally:
                    response.close()
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("EPG source request failed attempt=%s error_type=%s", attempt + 1, type(exc).__name__)
            if attempt + 1 < max(1, min(client.retries, 3)):
                time.sleep(min(2 ** attempt, 4))
    raise RuntimeError("EPG source could not be downloaded") from last_error


@contextmanager
def cached_source(client: HttpClient, url: str, *, cache_dir: Path | None,
                  ttl_seconds: int, now: datetime, headers: dict | None = None,
                  max_bytes: int = UPSTREAM_MAX_BYTES, lkg_seconds: int = 0):
    """Yield a file without loading an entire XMLTV body in memory.

    A retained source is explicitly dated; its caller must still validate the
    actual programme interval. Download failure never refreshes its mtime.
    """
    prune_cache(cache_dir, now=now)
    cache_limit = _budget("IPTV_EPG_CACHE_MAX_BYTES", CACHE_MAX_BYTES)
    max_bytes = min(max_bytes, _budget("IPTV_EPG_UPSTREAM_MAX_BYTES", UPSTREAM_MAX_BYTES), cache_limit // 2)
    target = cache_dir / (_cache_key(url, headers) + ".data") if cache_dir else None
    previous = None
    if target and target.is_file() and not target.is_symlink():
        stat = target.stat()
        age = now.timestamp() - stat.st_mtime
        if 0 < stat.st_size <= max_bytes and 0 <= age < max(ttl_seconds, lkg_seconds):
            previous = CachedSource(target, "lkg", datetime.fromtimestamp(stat.st_mtime, timezone.utc))
            if age < ttl_seconds:
                try:
                    yield CachedSource(target, "cache", previous.fetched_at)
                except (ValueError, ET.ParseError):
                    # A malformed cache left by an older version must not keep
                    # poisoning every request until its TTL expires.
                    target.unlink(missing_ok=True)
                    raise
                return
    temporary_path = None
    try:
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("wb", prefix="epg-", suffix=".tmp", dir=cache_dir, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            _headroom(temporary_path.parent)
            protected = (temporary_path,) + ((target,) if target else ())
            _download(client, url, temporary_path, max_bytes=max_bytes, headers=headers,
                      cache_dir=cache_dir, protected=protected, now=now)
        except (OSError, RuntimeError, ValueError):
            if previous is not None and lkg_seconds:
                yield previous
                return
            raise
        if target:
            # The consumer parses the temporary inside this context. Promote
            # only if that validation returns normally, preserving a previously
            # valid payload when an HTTP 200 contains malformed XML/JSON.
            yield CachedSource(temporary_path, "network", now)
            temporary_path.replace(target)
            os.utime(target, (now.timestamp(), now.timestamp()))
            prune_cache(cache_dir, now=now, protected=(target,))
        else:
            yield CachedSource(temporary_path, "network", now)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
