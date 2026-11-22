"""Private bindings between published entries and their exact sampled contexts."""

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from legal_iptv.io import write_json_atomic
from legal_iptv.io.stream_status_store import read_stream_status
from legal_iptv.models import Channel
from legal_iptv.services.link_validator import media_record_is_confirmed
from legal_iptv.services.validation_schedule import context_for_channel, matching_record


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_selection_proof(
    channels: list[Channel],
    output: Path,
    status_path: Path,
    *,
    max_age_seconds: int = 86400,
    min_ttl_seconds: int = 900,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    if type(min_ttl_seconds) is not int or min_ttl_seconds < 0:
        raise ValueError("Selection proof minimum validity is invalid")
    if not 1 <= max_age_seconds <= 86400:
        raise ValueError("Selection proof age limit is invalid")
    records = read_stream_status(status_path).get("urls", {})
    bindings = []
    for channel in channels:
        context = context_for_channel(channel)
        if (
            context.expired(now)
            or (
                context.expires_at is not None
                and (context.expires_at - now).total_seconds() < min_ttl_seconds
            )
            or not media_record_is_confirmed(
                matching_record(records.get(context.url), context),
                now=now,
                max_age_seconds=max_age_seconds,
            )
        ):
            raise ValueError("Selected entry lacks valid sampled media evidence")
        if not channel.tvg_id:
            raise ValueError("Selected entry lacks a published identity")
        exported = channel.stream_url + (
            "|" + urlencode(channel.request_headers) if channel.request_headers else ""
        )
        bindings.append(
            {
                "id": channel.tvg_id,
                "url_sha256": _digest(channel.stream_url),
                "context_id": context.id,
                "entry_sha256": _digest(exported),
                "expires_at": (
                    context.expires_at.isoformat()
                    if context.expires_at is not None
                    else None
                ),
            }
        )
    write_json_atomic(
        output,
        {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "max_age_seconds": max_age_seconds,
            "bindings": bindings,
        },
    )
    os.chmod(output, 0o600)


def _entries(path: Path) -> list[tuple[str, str, str]]:
    entries = []
    pending = None
    with path.open(encoding="utf-8-sig") as stream:
        first = stream.readline().strip()
        if not first.startswith("#EXTM3U"):
            raise ValueError("Invalid playlist")
        for line in stream:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                if pending is not None:
                    raise ValueError("Invalid playlist")
                match = re.search(r'\btvg-id="([^"\r\n]+)"', line)
                if match is None:
                    raise ValueError("Published entry lacks identity")
                pending = match.group(1)
            elif line and not line.startswith("#"):
                if pending is None:
                    raise ValueError("Invalid playlist")
                entries.append((pending, line.split("|", 1)[0], line))
                pending = None
    if pending is not None:
        raise ValueError("Invalid playlist")
    return entries


def verify_selection_proof(
    playlist: Path,
    proof: Path,
    status_path: Path,
    *,
    companion: Path | None = None,
    min_ttl_seconds: int = 900,
    now: datetime | None = None,
) -> None:
    """Verify unchanged entries against the shared policy without probing media."""
    now = now or datetime.now(UTC)
    if type(min_ttl_seconds) is not int or min_ttl_seconds < 0:
        raise ValueError("Selection proof minimum validity is invalid")
    value = json.loads(proof.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
    ):
        raise ValueError("Invalid selection proof")
    maximum = value.get("max_age_seconds")
    if type(maximum) is not int or not 1 <= maximum <= 86400:
        raise ValueError("Invalid selection proof")
    generated = datetime.fromisoformat(value.get("generated_at", ""))
    if generated.tzinfo is None or not 0 <= (now - generated).total_seconds() <= 86400:
        raise ValueError("Selection proof is expired or from the future")
    bindings = value.get("bindings")
    if not isinstance(bindings, list) or len(bindings) > 20000:
        raise ValueError("Invalid selection proof")
    index = {}
    for binding in bindings:
        if (
            not isinstance(binding, dict)
            or not isinstance(binding.get("id"), str)
            or not binding["id"]
            or len(binding["id"]) > 500
            or any(
                not isinstance(binding.get(key), str)
                or re.fullmatch(r"[a-f0-9]{64}", binding[key]) is None
                for key in ("url_sha256", "context_id", "entry_sha256")
            )
        ):
            raise ValueError("Invalid selection binding")
        if binding.get("expires_at") is not None:
            expiry = datetime.fromisoformat(binding["expires_at"])
            if (
                expiry.tzinfo is None
                or (expiry - now).total_seconds() < min_ttl_seconds
            ):
                raise ValueError(
                    "Selected transport expires before the minimum validity window"
                )
        key = (binding["id"], binding["url_sha256"], binding["entry_sha256"])
        if key in index:
            raise ValueError("Duplicate selection binding")
        index[key] = binding["context_id"]
    entries = _entries(playlist)
    keys = [
        (identifier, _digest(url), _digest(exported))
        for identifier, url, exported in entries
    ]
    if Counter(keys) != Counter(index.keys()):
        raise ValueError("Selection proof does not match the published entries")
    records = read_stream_status(status_path).get("urls", {})
    for key, (_, url, _) in zip(keys, entries):
        record = records.get(url)
        if isinstance(record, dict) and isinstance(record.get("contexts"), dict):
            record = record["contexts"].get(index[key])
        if (
            not isinstance(record, dict)
            or record.get("context_id") != index[key]
            or not media_record_is_confirmed(record, now=now, max_age_seconds=maximum)
        ):
            raise ValueError("Published entry lacks valid sampled media evidence")
    if companion is not None:
        subset = Counter(
            (identifier, _digest(url), _digest(exported))
            for identifier, url, exported in _entries(companion)
        )
        if subset - Counter(keys):
            raise ValueError("Companion proof differs from the main selection")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("playlist", "proof", "status"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--companion", type=Path)
    parser.add_argument("--min-ttl", type=int, default=900)
    args = parser.parse_args()
    try:
        verify_selection_proof(
            args.playlist,
            args.proof,
            args.status,
            companion=args.companion,
            min_ttl_seconds=args.min_ttl,
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        parser.exit(1, "Selection proof verification failed\n")


if __name__ == "__main__":
    main()
