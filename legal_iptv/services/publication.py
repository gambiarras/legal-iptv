from urllib.parse import parse_qsl, urlsplit

from legal_iptv.models import Channel
from legal_iptv.services.video_platform_transport import (
    PRIVATE_HEADERS,
    PRIVATE_NAMES,
    _private_header_context,
    is_public_video_platform_hls,
)

SENSITIVE_HEADER_NAMES = PRIVATE_HEADERS
SENSITIVE_QUERY_NAMES = PRIVATE_NAMES | {"sig", "signature"}


def _has_sensitive_headers(headers) -> bool:
    return isinstance(headers, dict) and _private_header_context(headers)


def _has_sensitive_url(url) -> bool:
    if not isinstance(url, str) or not url:
        return True
    try:
        parsed = urlsplit(url)
        # Accessing hostname/port also rejects malformed bracket/port syntax.
        if (
            not parsed.scheme
            or not parsed.hostname
            or parsed.port is not None
            and parsed.port < 1
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in url
            or any(char.isspace() or ord(char) < 32 for char in url)
        ):
            return True
        return any(
            name.strip().casefold() in SENSITIVE_QUERY_NAMES
            for name, _ in parse_qsl(parsed.query, keep_blank_values=True)
        )
    except (ValueError, TypeError):
        return True


def _has_supported_kodi_drm(channel: Channel) -> bool:
    if not channel.drm:
        return False
    drm_type = str(
        channel.drm.get("type") or channel.drm.get("key_system") or ""
    ).casefold()
    return drm_type in {"widevine", "com.widevine.alpha"} and bool(
        channel.drm.get("license_url")
    )


def is_exportable(channel: Channel, player_profile: str) -> bool:
    renewable = is_public_video_platform_hls(channel)
    if channel.source_type == "video_platform" and not renewable:
        return False
    if (
        not channel.stream_url
        or channel.removed
        or (
            (not channel.publishable_static or channel.requires_dynamic_resolution)
            and not renewable
        )
        or channel.secret_refs
        or channel.delivery_mode in {"dai", "ssai"}
        or _has_sensitive_headers(channel.request_headers)
        or (_has_sensitive_url(channel.stream_url) and not renewable)
    ):
        return False
    if channel.drm and player_profile != "kodi":
        return False
    if channel.drm and (
        _has_sensitive_url(channel.drm.get("license_url"))
        or _has_sensitive_headers(channel.drm.get("license_headers"))
    ):
        return False
    if channel.drm and not _has_supported_kodi_drm(channel):
        return False
    return True
