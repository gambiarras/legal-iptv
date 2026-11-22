"""Narrow export exception for public, expiring VideoPlatform HLS media."""

from __future__ import annotations

import re
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit

from legal_iptv.local_config import runtime_value

if TYPE_CHECKING:
    from legal_iptv.models import Channel


def source_settings(role: str) -> dict:
    """Read the component-specific private role configuration."""
    value = runtime_value(role, {})
    return value if isinstance(value, dict) else {}


PRIVATE_NAMES = {
    "username", "password", "passwd", "apikey", "api-key", "api_key", "x-api-key", "access_token",
    "auth", "authorization", "credential", "cookie", "cookies", "hdnea",
    "hdntl", "hdnts", "jwt", "key-pair-id", "policy", "session", "token",
    "oauth", "x-amz-credential", "x-amz-security-token", "x-amz-signature",
}
PRIVATE_HEADERS = PRIVATE_NAMES | {"proxy-authorization", "set-cookie"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")


def _hosts(name: str) -> tuple[str, ...]:
    """Use only explicitly configured host lists, with exact DNS boundaries."""
    value = source_settings("video_platform").get(name, ())
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(host.casefold() for host in value
                 if isinstance(host, str) and host and not any(char.isspace() for char in host))


def _public_https(url: str | None) -> SplitResult | None:
    """Parse a credential-free HTTPS URL, rejecting ambiguous authority syntax."""
    if not isinstance(url, str) or not url:
        return None
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 443} or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in url)
            or "\\" in url
        ):
            return None
        if any(name.strip().casefold() in PRIVATE_NAMES
               for name, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            return None
        return parsed
    except (ValueError, TypeError):
        return None


def _public_source(url: str | None) -> bool:
    """Allow only recognized public VideoPlatform video or live-channel page shapes."""
    parsed = _public_https(url)
    if parsed is None:
        return False
    host = parsed.hostname.casefold()
    if any(key.strip().casefold() in {"sig", "signature"}
           for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return False
    if host in _hosts("short_hosts"):
        return bool(VIDEO_ID.fullmatch(parsed.path.strip("/")))
    if host not in _hosts("page_hosts"):
        return False
    settings = source_settings("video_platform")
    watch_path = urlsplit(settings.get("watch_url", "")).path
    if watch_path and parsed.path == watch_path:
        ids = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True)
               if key == settings.get("video_id_query", "v")]
        return len(ids) == 1 and bool(VIDEO_ID.fullmatch(ids[0]))
    patterns = settings.get("source_path_patterns", (
        r"/live/[A-Za-z0-9_-]{11}",
        r"/(?:@[^/?#\s]+|(?:channel|c|user)/[^/?#\s]+)/live",
    ))
    try:
        return any(re.fullmatch(pattern, parsed.path.rstrip("/")) for pattern in patterns)
    except (TypeError, re.error):
        return False


def _private_header_context(headers: dict[str, str]) -> bool:
    """A public media URL must not rely on a credential-bearing header URL."""
    for name, value in headers.items():
        if str(name).strip().casefold() in PRIVATE_HEADERS:
            return True
        try:
            text = str(value)
            parsed = urlsplit(text)
            port = parsed.port
            if ((parsed.scheme in {"http", "https"} and not parsed.hostname)
                    or (parsed.netloc and (not parsed.hostname or port == 0
                        or "\\" in text or any(char.isspace() or ord(char) < 32 for char in text)))):
                return True
            if parsed.username is not None or parsed.password is not None or any(
                key.strip().casefold() in PRIVATE_NAMES | {"sig", "signature"}
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            ):
                return True
        except (TypeError, ValueError):
            return True
    return False


def is_media_hls_url(url: str | None) -> bool:
    """Recognize declared HLS on reviewed media hosts rather than arbitrary HTTPS."""
    if not url:
        return False
    parsed = _public_https(url)
    if parsed is None:
        return False
    host = parsed.hostname.casefold()
    if not any(host == approved or host.endswith("." + approved) for approved in _hosts("media_hosts")):
        return False
    path = unquote(parsed.path).casefold()
    parts = [part for part in path.split("/") if part]
    if any(part in PRIVATE_NAMES for part in parts):
        return False
    patterns = source_settings("video_platform").get("media_path_patterns", (
        r"^/api/manifest/(?:hls_variant|hls_playlist)(?:/|$)",
    ))
    try:
        return path.endswith(".m3u8") or any(re.match(pattern, path) for pattern in patterns)
    except (TypeError, re.error):
        return False


def media_hls_expiry(url: str | None) -> datetime | None:
    """Use the earliest explicit expiry present in the media URL itself."""
    if not is_media_hls_url(url):
        return None
    parsed = urlsplit(url)
    settings = source_settings("video_platform")
    expiry_fields = settings.get("expiry_fields", ("expire", "expires", "exp"))
    values = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True)
              if key.casefold() in expiry_fields]
    parts = [part for part in unquote(parsed.path).split("/") if part]
    for index, part in enumerate(parts):
        if part.casefold() == settings.get("expiry_path_field", "expire"):
            if index + 1 == len(parts):
                return None
            values.append(parts[index + 1])
    try:
        if not values:
            return None
        return min(datetime.fromtimestamp(int(value), tz=timezone.utc) for value in values)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def is_public_video_platform_hls(channel: Channel, *, min_ttl_seconds: int = 900,
                          now: datetime | None = None) -> bool:
    """Allow only recently usable public media while preserving dynamic capability flags."""
    if (
        channel.source_type != "video_platform" or channel.status != "resolved"
        or channel.removed or channel.secret_refs or channel.drm is not None
        or str(channel.delivery_mode or "").casefold() != "direct"
        or channel.protocol not in {None, "hls", "https"}
        or not _public_source(channel.source_url)
        or _private_header_context(channel.request_headers)
    ):
        return False
    expiry = media_hls_expiry(channel.stream_url)
    if expiry is None:
        return False
    if channel.ttl_seconds is not None:
        if isinstance(channel.ttl_seconds, bool) or not isinstance(channel.ttl_seconds, (int, float)):
            return False
        try:
            ttl = float(channel.ttl_seconds)
            if not math.isfinite(ttl) or not ttl.is_integer() or ttl < min_ttl_seconds:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
    if channel.expires_at is not None:
        try:
            advertised = datetime.fromisoformat(channel.expires_at.replace("Z", "+00:00"))
            if advertised.tzinfo is None:
                return False
            expiry = min(expiry, advertised)
        except (TypeError, ValueError, AttributeError):
            return False
    now = now or datetime.now(timezone.utc)
    return now.tzinfo is not None and (expiry - now).total_seconds() >= min_ttl_seconds
