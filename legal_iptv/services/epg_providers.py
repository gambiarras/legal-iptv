import gzip
import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources

from legal_iptv.clients import HttpClient


RESOURCE_NAME = "epg_providers.json"


@dataclass(slots=True, frozen=True)
class EpgProvider:
    id: str
    url: str
    format: str = "xmltv"
    headers: dict[str, str] | None = None

    def fetch(self, client: HttpClient) -> ET.Element:
        payload = client.get_bytes(self.url, headers=self.headers)
        if self.format == "json":
            return json_epg_to_xmltv(json.loads(payload))
        if payload.startswith(b"\x1f\x8b") or self.url.endswith(".gz"):
            payload = gzip.decompress(payload)
        root = ET.fromstring(payload)
        if root.tag != "tv":
            raise ValueError("EPG provider did not return XMLTV")
        return root


def load_epg_providers() -> list[EpgProvider]:
    resource = resources.files("legal_iptv.resources").joinpath(RESOURCE_NAME)
    raw = json.loads(resource.read_text(encoding="utf-8"))
    providers: list[EpgProvider] = []

    for item in raw:
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
            suffix = f"_{index}" if len(urls) > 1 else ""
            providers.append(
                EpgProvider(
                    id=f"{item['id']}{suffix}",
                    url=url,
                    format=provider_format,
                    headers=headers,
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
        channel_id = channel.get("id") or channel.get("tvg_id") or channel.get("channel_id")
        if not channel_id:
            continue
        element = ET.SubElement(root, "channel", {"id": str(channel_id)})
        ET.SubElement(element, "display-name").text = str(
            channel.get("name") or channel.get("display_name") or channel_id
        )
        logo = channel.get("logo") or channel.get("icon")
        if logo:
            ET.SubElement(element, "icon", {"src": str(logo)})

        programmes = channel.get("programmes") or channel.get("programs") or channel.get("schedule") or ()
        if not isinstance(programmes, list):
            continue
        for programme in programmes:
            if not isinstance(programme, dict):
                continue
            start = _xmltv_timestamp(programme.get("start") or programme.get("start_time"))
            stop = _xmltv_timestamp(
                programme.get("stop") or programme.get("end") or programme.get("end_time")
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
            category = programme.get("category")
            if category:
                ET.SubElement(programme_element, "category", {"lang": "pt"}).text = str(category)
    return root


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
