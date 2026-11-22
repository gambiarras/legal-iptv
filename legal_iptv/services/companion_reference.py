from legal_iptv.local_config import runtime_value, snapshot_value

"""Read public channel identities without retaining Base Reference stream credentials."""

import hashlib
import html
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from legal_iptv.clients import HttpClient
from legal_iptv.io import write_json_atomic

BASE_REFERENCE_BASE = runtime_value('reference_base_url', 'https://reference-base-url.example.invalid/')
DISABLED_URL = runtime_value('reference_disabled_url', 'https://reference-disabled-url.example.invalid/')
DIRECTORY_CATALOG_URL = runtime_value('directory_url', 'https://directory-url.example.invalid/')
REFERENCE_VERSION = 3
QUALITY = re.compile(r"\b(?:2160p|1080p|720p|480p|4k|uhd|fhd|hd|sd|hevc|h[ .]?26[45])\b", re.I)


def identity_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]", "", QUALITY.sub(" ", text))


def _host(url: str) -> str:
    try:
        return (urlsplit(url.strip()).hostname or "").casefold()
    except ValueError:
        return ""


def _disabled_hosts(payload) -> set[str]:
    # The Android endpoint uses `desativados`; accept documented legacy lists too.
    if isinstance(payload, dict):
        payload = payload.get("desativados", payload.get("hosts", payload.get("disabled", [])))
    if not isinstance(payload, list):
        raise ValueError("Invalid disabled-host reference")
    result = set()
    for item in payload:
        if isinstance(item, dict):
            item = item.get("host", item.get("servidor", ""))
        if isinstance(item, str) and item.strip():
            result.add(_host(item if "://" in item else "https://" + item))
    return result - {""}


def _catalogue(text: str) -> list[tuple[str, set[str]]]:
    if len(text) > 8_000_000:
        raise ValueError("Base Reference catalogue exceeds size limit")
    rows: list[tuple[str, set[str]]] = []
    name = ""
    hosts: set[str] = set()
    for line in text.splitlines():
        field, separator, value = line.strip().partition(":")
        if not separator or line.lstrip().startswith("#"):
            continue
        if field.casefold() == "canal":
            if name:
                rows.append((name, hosts))
            name, hosts = value.strip(), set()
        elif field.casefold() == "fonte":
            host = _host(value)
            if host:
                hosts.add(host)
    if name:
        rows.append((name, hosts))
    return rows


def _extras(text: str) -> list[tuple[str, set[str]]]:
    grouped: dict[str, set[str]] = {}
    name = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            # EXTINF display separator is the first comma outside quoted attributes.
            tvg_id = re.search(r'\btvg-id="([^\"]+)"', line)
            quoted = False
            name = ""
            for index, char in enumerate(line):
                if char == '"':
                    quoted = not quoted
                elif char == "," and not quoted:
                    name = line[index + 1:].strip()
                    break
            name = tvg_id.group(1) if tvg_id and tvg_id.group(1).strip() else re.sub(
                r"\s*\([^)]+\)$", "", name).strip()
        elif name and line and not line.startswith("#"):
            host = _host(line.split("|", 1)[0])
            if host:
                grouped.setdefault(name, set()).add(host)
    return list(grouped.items())


def _android_key(name: str) -> str:
    name = re.sub(r"^[A-Z]{2}\s*[-|]\s*", "", html.unescape(name))
    text = unicodedata.normalize("NFD", name)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn").casefold()
    tokens = re.sub(r"[^a-z0-9]", " ", text).split()
    while len(tokens) > 1 and tokens[-1] in {"hd", "sd", "fhd", "uhd", "4k", "br"}:
        tokens.pop()
    return " ".join(tokens)


def build_reference(catalogue: str, extras: str, disabled, directory=None) -> dict:
    disabled_hosts = _disabled_hosts(disabled)
    published = _extras(extras)
    extra_by_key = {_android_key(name): hosts for name, hosts in published}
    used = set()
    merged = []
    for name, hosts in _catalogue(catalogue):
        key = _android_key(name)
        if key in extra_by_key:
            used.add(key)
            hosts = hosts | extra_by_key[key]
        merged.append((name, hosts))
    merged.extend((name, hosts) for name, hosts in published if _android_key(name) not in used)
    names = sorted(name for name, hosts in merged if hosts - disabled_hosts)
    has_directory = directory is not None
    if directory is None:
        directory = []
    if isinstance(directory, dict):
        if directory.get("ok") is not True:
            raise ValueError("Directory Catalog reference reported failure")
        directory = directory.get("channels")
    if not isinstance(directory, list):
        raise ValueError("Invalid Directory Catalog reference")
    directory_names = sorted({row["name"].strip() for row in directory
                              if isinstance(row, dict) and isinstance(row.get("name"), str)
                              and row["name"].strip()})
    if len(names) < 20 or (has_directory and len(directory_names) < 20):
        raise ValueError("Reference unexpectedly empty or incomplete")
    # Deliberately omit media URLs, hosts and raw payloads from cache/diagnostics.
    return {
        "version": REFERENCE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_reference_channels": names,
        "directory_catalog_channels": directory_names,
        "catalogue_sha256": hashlib.sha256(catalogue.encode()).hexdigest(),
        "extras_sha256": hashlib.sha256(extras.encode()).hexdigest(),
        "disabled_sha256": hashlib.sha256(json.dumps(disabled, sort_keys=True).encode()).hexdigest(),
        "sources": [snapshot_value("reference_base_url", BASE_REFERENCE_BASE) + "catalogo.txt", snapshot_value("reference_base_url", BASE_REFERENCE_BASE) + "canais.txt",
                    snapshot_value("reference_disabled_url", DISABLED_URL)] + ([snapshot_value("directory_url", DIRECTORY_CATALOG_URL)] if has_directory else []),
    }


def validate_reference(payload: dict, *, max_age_seconds: int = 172800) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Companion reference must be an object")
    if payload.get("version") not in {2, REFERENCE_VERSION}:
        raise ValueError("Unsupported Companion reference version")
    payload = dict(payload)
    for legacy, current in runtime_value("reference_field_aliases", {}).items():
        if current not in payload and legacy in payload:
            payload[current] = payload[legacy]
    generated = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise ValueError("Reference timestamp requires timezone")
    age = (datetime.now(timezone.utc) - generated).total_seconds()
    if age < -300 or age > max_age_seconds:
        raise ValueError("Companion reference is expired or from the future")
    for field in ("base_reference_channels", "directory_catalog_channels"):
        names = payload.get(field)
        minimum = 20 if field == "base_reference_channels" or payload["version"] == 2 else 0
        if not isinstance(names, list) or not minimum <= len(names) <= 5000 or not all(
            isinstance(name, str) and 0 < len(name) <= 300 and "://" not in name
            for name in names
        ):
            raise ValueError("Invalid Companion channel identities")
    return payload


def load_reference(client: HttpClient, cache_file: Path, *, local_file: Path | None = None) -> dict:
    if local_file:
        return validate_reference(json.loads(local_file.read_text(encoding="utf-8")))
    cached = None
    try:
        cached = validate_reference(json.loads(cache_file.read_text(encoding="utf-8")))
        validate_reference(cached, max_age_seconds=21600)
        return cached
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        payload = build_reference(
            client.get_text(snapshot_value("reference_base_url", BASE_REFERENCE_BASE) + "catalogo.txt"),
            client.get_text(snapshot_value("reference_base_url", BASE_REFERENCE_BASE) + "canais.txt"),
            client.get_json(snapshot_value("reference_disabled_url", DISABLED_URL)),
        )
        validate_reference(payload)
        write_json_atomic(cache_file, payload)
        return payload
    except Exception:
        if cached is not None:
            # Never extend the timestamp of the last known reference on failure.
            validate_reference(cached)
            return {**cached, "retained_reference": True}
        raise RuntimeError("Companion references unavailable; refusing an unfiltered playlist") from None
