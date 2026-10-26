import json
import logging
from pathlib import Path

from legal_iptv.io.json_store import write_json_atomic
from legal_iptv.services.stream_status import TEMPORARILY_UNAVAILABLE_STATUS


logger = logging.getLogger(__name__)


def _read_status_records(status_file: Path) -> dict[str, dict]:
    if not status_file.exists():
        return {}

    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read stream status file path=%s", status_file)
        return {}

    records = payload.get("urls", {})
    if not isinstance(records, dict):
        return {}

    return {url: record for url, record in records.items() if isinstance(record, dict)}


def _write_stream_status_records(
    status_file: Path,
    records_by_url: dict[str, dict],
    *,
    generated_at: str,
    validated_urls: int,
    cached_urls: int,
) -> None:
    status_counts = {
        "active": 0,
        "offline": 0,
        "unknown": 0,
        TEMPORARILY_UNAVAILABLE_STATUS: 0,
    }
    source_counts: dict[str, int] = {}
    source_type_counts: dict[str, int] = {}

    for record in records_by_url.values():
        status = record.get("status")
        if status in status_counts:
            status_counts[status] += 1

        for source in record.get("sources", []):
            source_counts[source] = source_counts.get(source, 0) + 1

        for source_type in record.get("source_types", []):
            source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1

    payload = {
        "generated_at": generated_at,
        "summary": {
            "total_urls": len(records_by_url),
            "validated_urls": validated_urls,
            "cached_urls": cached_urls,
            "status_counts": status_counts,
            "source_counts": source_counts,
            "source_type_counts": source_type_counts,
        },
        "urls": {
            url: records_by_url[url]
            for url in sorted(records_by_url)
        },
    }

    write_json_atomic(status_file, payload)
