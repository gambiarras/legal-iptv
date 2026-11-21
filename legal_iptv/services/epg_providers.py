import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from legal_iptv.local_config import resource_path
from pathlib import Path
from urllib.parse import urlencode

from legal_iptv.clients import HttpClient


RESOURCE_NAME = "epg_providers.json"
SOLR_PROGRAMME_BATCH_SIZE = 5
SOLR_PROGRAMME_PAGE_SIZE = 250


@dataclass(slots=True, frozen=True)
class EpgProvider:
    id: str
    url: str
    format: str = "xmltv"
    headers: dict[str, str] | None = None
    city: str | None = None
    days: int = 2

    def fetch(self, client: HttpClient, *, cache_dir: Path | None = None,
              channel_ids: Collection[str] | None = None, now: datetime | None = None,
              days: int = 3, inventory: dict | None = None,
              channel_names: dict[str, str] | None = None) -> ET.Element:
        from legal_iptv.services.guide import _programme_window, read_xmltv_source
        from legal_iptv.services.epg_cache import cached_source

        now = now or datetime.now(timezone.utc)
        if self.format in {"channel_schedule", "schedule_site_a", "schedule_site_b", "official_schedule_a", "official_schedule_b", "schedule_site_b_mapped", "schedule_site_a_mapped"}:
            from legal_iptv.services.epg_collectors import (
                fetch_channel_schedule, fetch_html_schedules, fetch_official_schedule,
                fetch_mapped_schedules,
            )
            if self.format == "channel_schedule":
                root = fetch_channel_schedule(client, self.url, cache_dir=cache_dir, days=days, now=now)
            elif self.format in {"official_schedule_a", "official_schedule_b"}:
                root = fetch_official_schedule(client, self.url, self.format, cache_dir=cache_dir,
                                              now=now, days=days, channel_ids=channel_ids)
            elif self.format.endswith("_mapped"):
                root = fetch_mapped_schedules(client, self.format.removesuffix("_mapped"),
                                             cache_dir=cache_dir, now=now, channel_ids=channel_ids,
                                             channel_names=channel_names)
            else:
                root = fetch_html_schedules(client, self.url, self.format, cache_dir=cache_dir,
                                            now=now, channel_ids=channel_ids)
        elif self.format in {"solr", "solr_json"}:
            root = solr_epg_to_xmltv(client, self.url, city=self.city or "", days=days, headers=self.headers)
        elif self.format == "json":
            with cached_source(client, self.url, cache_dir=cache_dir, ttl_seconds=1800,
                               lkg_seconds=21600, now=now, headers=self.headers) as cached:
                with cached.path.open("r", encoding="utf-8") as source:
                    root = json_epg_to_xmltv(json.load(source))
        else:
            with cached_source(client, self.url, cache_dir=cache_dir, ttl_seconds=1800,
                               lkg_seconds=21600, now=now, headers=self.headers) as cached:
                root = read_xmltv_source(cached.path, channel_ids=channel_ids, now=now,
                                         days=days, inventory=inventory)
                root.set("data-cache-origin", cached.origin)
                root.set("data-collected-at", cached.fetched_at.isoformat())
                return root
        # Non-XMLTV collectors are small already, but still observe the same
        # final selection/window and publish their names into the private index.
        wanted = set(channel_ids) if channel_ids is not None else None
        filtered = ET.Element("tv", dict(root.attrib))
        for channel in root.findall("channel"):
            identifier = channel.get("id", "")
            if inventory is not None and identifier:
                record = inventory.setdefault(identifier, {"id": identifier, "names": [], "programme_count": 0, "current": False, "future": False})
                for name in channel.findall("display-name"):
                    text = (name.text or "").strip()
                    if text and text not in record["names"]:
                        record["names"].append(text[:256])
            if wanted is None or identifier in wanted:
                filtered.append(channel)
        horizon = now + timedelta(days=max(1, min(days, 7)))
        for programme in root.findall("programme"):
            window = _programme_window(programme, now, horizon)
            if window is None:
                continue
            identifier = programme.get("channel")
            if inventory is not None and identifier in inventory:
                record = inventory[identifier]
                record["programme_count"] += 1
                record["current"] |= window[0] <= now < window[1]
                record["future"] |= window[0] > now
            if wanted is None or identifier in wanted:
                filtered.append(programme)
        return filtered


def load_epg_providers() -> list[EpgProvider]:
    resource = resource_path(RESOURCE_NAME)
    raw = json.loads(resource.read_text(encoding="utf-8"))
    providers: list[EpgProvider] = []
    seen_contexts = set()

    for item in raw:
        enabled = os.environ.get(str(item.get("enabled_env") or ""), str(item.get("enabled", True)))
        if enabled.casefold() in {"0", "false", "no", "off"}:
            continue
        provider_format = str(
            os.environ.get(str(item.get("format_env") or ""), item.get("format", "xmltv"))
        ).casefold()
        headers = None
        headers_env = item.get("headers_env")
        if headers_env and os.environ.get(str(headers_env)):
            raw_headers = json.loads(os.environ[str(headers_env)])
            if isinstance(raw_headers, dict):
                headers = {
                    str(key): str(value)
                    for key, value in raw_headers.items()
                }
        city = None
        city_env = item.get("city_env")
        if city_env:
            city = os.environ.get(str(city_env), str(item.get("city") or "")) or None
        days = int(
            os.environ.get(
                str(item.get("days_env") or ""),
                str(item.get("days", 2)),
            )
        )
        urls: list[str] = []
        url_env = item.get("url_env")
        if url_env and os.environ.get(str(url_env)):
            urls.append(os.environ[str(url_env)])
        urls_env = item.get("urls_env")
        if urls_env and os.environ.get(str(urls_env)):
            urls.extend(
                value.strip()
                for value in os.environ[str(urls_env)].split(",")
                if value.strip()
            )
        elif not url_env:
            urls.extend(str(value) for value in item.get("urls", ()) if value)

        for index, url in enumerate(urls, start=1):
            context = (url, provider_format, tuple(sorted((headers or {}).items())), city, days)
            if context in seen_contexts:
                continue
            seen_contexts.add(context)
            suffix = f"_{index}" if len(urls) > 1 else ""
            providers.append(
                EpgProvider(
                    id=f"{item['id']}{suffix}",
                    url=url,
                    format=provider_format,
                    headers=headers,
                    city=city,
                    days=days,
                )
            )
    return providers


def json_epg_to_xmltv(payload) -> ET.Element:
    root = ET.Element("tv", {"generator-info-name": "local-xmltv-generator"})
    channels = payload.get("channels") if isinstance(payload, dict) else payload
    if not isinstance(channels, list):
        raise ValueError("JSON EPG channels must be a list")

    for channel in channels:
        if not isinstance(channel, dict):
            continue
        custom_fields = channel.get("custom_fields")
        if not isinstance(custom_fields, dict):
            custom_fields = {}
        channel_id = channel.get("id") or channel.get("tvg_id") or channel.get("channel_id")
        if not channel_id:
            continue
        element = ET.SubElement(root, "channel", {"id": str(channel_id)})
        ET.SubElement(element, "display-name").text = str(
            channel.get("name")
            or channel.get("display_name")
            or channel.get("title")
            or custom_fields.get("channel_display_name")
            or custom_fields.get("channel_name")
            or channel_id
        )
        logo = channel.get("logo") or channel.get("icon") or custom_fields.get("channel_logo")
        if logo:
            ET.SubElement(element, "icon", {"src": str(logo)})

        programmes = channel.get("programmes") or channel.get("programs") or channel.get("schedule") or ()
        if not isinstance(programmes, list):
            continue
        for programme in programmes:
            if not isinstance(programme, dict):
                continue
            start = _xmltv_timestamp(
                programme.get("start")
                or programme.get("start_time")
                or programme.get("startTime")
            )
            stop = _xmltv_timestamp(
                programme.get("stop")
                or programme.get("end")
                or programme.get("end_time")
                or programme.get("endTime")
            )
            title = programme.get("title") or programme.get("name")
            if not start or not stop or not title:
                continue
            programme_element = ET.SubElement(
                root,
                "programme",
                {"channel": str(channel_id), "start": str(start), "stop": str(stop)},
            )
            ET.SubElement(programme_element, "title", {"lang": "pt"}).text = str(title)
            description = programme.get("description") or programme.get("desc")
            if description:
                ET.SubElement(programme_element, "desc", {"lang": "pt"}).text = str(description)
            macros = programme.get("macros")
            if not isinstance(macros, dict):
                macros = {}
            category = (
                programme.get("category")
                or programme.get("genre")
                or macros.get("content_genre")
                or macros.get("content_categories")
            )
            if category:
                ET.SubElement(programme_element, "category", {"lang": "pt"}).text = str(category)
            rating = programme.get("rating") or programme.get("parentalRating")
            if rating:
                rating_element = ET.SubElement(programme_element, "rating", {"system": "BR"})
                ET.SubElement(rating_element, "value").text = str(rating)
    return root


def solr_epg_to_xmltv(
    client: HttpClient,
    base_url: str,
    *,
    city: str,
    days: int = 2,
    headers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> ET.Element:
    if not city.strip():
        raise ValueError("Solr EPG provider requires a city")
    base_url = base_url.rstrip("/")
    city_document = _resolve_solr_city(client, base_url, city, headers)
    city_id = city_document.get("id_cidade") or city_document.get("id")
    if city_id is None:
        raise ValueError("Solr EPG city has no id")

    channel_payload = _fetch_json(
        client,
        _query_url(
            f"{base_url}/canal/select",
            {
                "q": f"id_cidade:{city_id}",
                "wt": "json",
                "rows": 1000,
                "start": 0,
                "sort": "cn_canal asc",
                "fl": "id_canal st_canal cn_canal nome url_imagem id_cidade id_revel",
            },
        ),
        headers,
    )
    channel_documents = _solr_documents(channel_payload)
    root = ET.Element("tv", {"generator-info-name": "local-xmltv-generator"})
    channel_ids: dict[str, str] = {}
    reveal_ids: list[str] = []

    for channel in channel_documents:
        raw_channel_id = channel.get("id_canal") or channel.get("id")
        if raw_channel_id is None:
            continue
        channel_id = str(raw_channel_id)
        channel_ids[channel_id] = channel_id
        reveal_id = str(channel.get("id_revel") or f"{city_id}_{channel_id}")
        channel_ids[reveal_id] = channel_id
        reveal_ids.append(reveal_id)
        element = ET.SubElement(root, "channel", {"id": channel_id})
        ET.SubElement(element, "display-name").text = str(
            channel.get("st_canal") or channel.get("nome") or channel_id
        )
        logo = channel.get("url_imagem")
        if logo:
            ET.SubElement(element, "icon", {"src": str(logo)})

    if not reveal_ids:
        raise ValueError("Solr EPG provider returned no channels")

    now = now or datetime.now(timezone.utc)
    range_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    range_stop = range_start + timedelta(days=max(1, days) + 1) - timedelta(seconds=1)
    date_filter = (
        f"dh_inicio:[{range_start.strftime('%Y-%m-%dT%H:%M:%SZ')} TO "
        f"{range_stop.strftime('%Y-%m-%dT%H:%M:%SZ')}]"
    )

    for offset in range(0, len(reveal_ids), SOLR_PROGRAMME_BATCH_SIZE):
        batch = reveal_ids[offset : offset + SOLR_PROGRAMME_BATCH_SIZE]
        page_start = 0
        while True:
            programme_payload = _fetch_json(
                client,
                _query_url(
                    f"{base_url}/exibicao/select",
                    {
                        "q": f"id_revel:({' '.join(batch)}) AND id_cidade:{city_id}",
                        "wt": "json",
                        "rows": SOLR_PROGRAMME_PAGE_SIZE,
                        "start": page_start,
                        "sort": "id_canal asc,dh_inicio asc",
                        "fl": (
                            "dh_fim dh_inicio st_titulo titulo id_programa "
                            "id_canal id_cidade id_revel"
                        ),
                        "fq": date_filter,
                    },
                ),
                headers,
            )
            programme_documents = _solr_documents(programme_payload)
            for programme in programme_documents:
                raw_channel_id = programme.get("id_canal") or programme.get("id_revel")
                channel_id = channel_ids.get(str(raw_channel_id))
                if channel_id is None and raw_channel_id is not None:
                    channel_id = channel_ids.get(str(raw_channel_id).split("_")[-1])
                start = _xmltv_timestamp(programme.get("dh_inicio"))
                stop = _xmltv_timestamp(programme.get("dh_fim"))
                title = programme.get("titulo") or programme.get("st_titulo")
                if not channel_id or not start or not stop or not title:
                    continue
                element = ET.SubElement(
                    root,
                    "programme",
                    {"channel": channel_id, "start": start, "stop": stop},
                )
                ET.SubElement(element, "title", {"lang": "pt"}).text = str(title)

            if len(programme_documents) < SOLR_PROGRAMME_PAGE_SIZE:
                break
            page_start += len(programme_documents)
    return root


def _resolve_solr_city(
    client: HttpClient,
    base_url: str,
    configured_cities: str,
    headers: dict[str, str] | None,
) -> dict:
    last_error: Exception | None = None
    for candidate in (value.strip() for value in configured_cities.split(",")):
        if not candidate:
            continue
        name, state = _split_city(candidate)
        query = {
            "q": f"nome_novo:{_slug(name)}",
            "wt": "json",
            "rows": 1,
        }
        if state:
            query["fq"] = f"uf:{state}"
        try:
            payload = _fetch_json(
                client,
                _query_url(f"{base_url}/cidade/select", query),
                headers,
            )
        except Exception as exc:
            last_error = exc
            continue
        documents = _solr_documents(payload)
        if documents:
            return documents[0]
    if last_error:
        raise last_error
    raise ValueError("Configured Solr EPG city was not found")


def _split_city(value: str) -> tuple[str, str | None]:
    match = re.match(r"^(.*?)(?:\s*[-/]\s*([A-Za-z]{2}))?$", value.strip())
    if not match:
        return value.strip(), None
    return match.group(1).strip(), match.group(2).upper() if match.group(2) else None


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


def _query_url(url: str, query: dict[str, object]) -> str:
    return f"{url}?{urlencode(query)}"


def _fetch_json(
    client: HttpClient,
    url: str,
    headers: dict[str, str] | None,
):
    return json.loads(client.get_bytes(url, headers=headers))


def _solr_documents(payload) -> list[dict]:
    response = payload.get("response") if isinstance(payload, dict) else None
    documents = response.get("docs") if isinstance(response, dict) else None
    if not isinstance(documents, list):
        raise ValueError("Solr EPG response has no documents")
    return [item for item in documents if isinstance(item, dict)]


def _xmltv_timestamp(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y%m%d%H%M%S %z")
    text = str(value).strip()
    if len(text) >= 8 and text[:8].isdigit() and "T" not in text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.strftime("%Y%m%d%H%M%S %z")
