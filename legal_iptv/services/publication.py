from urllib.parse import parse_qsl, urlsplit

from legal_iptv.models import Channel


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "api-key",
}
SENSITIVE_QUERY_NAMES = {
    "access_token",
    "authorization",
    "credential",
    "hdnea",
    "hdntl",
    "hdnts",
    "key-pair-id",
    "policy",
    "session",
    "sig",
    "signature",
    "token",
    "x-amz-credential",
    "x-amz-security-token",
    "x-amz-signature",
}


def _has_sensitive_headers(headers) -> bool:
    return isinstance(headers, dict) and any(
        str(name).casefold() in SENSITIVE_HEADER_NAMES
        for name in headers
    )


def _has_sensitive_url(url) -> bool:
    if not isinstance(url, str) or not url:
        return False
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        return True
    return any(
        name.casefold() in SENSITIVE_QUERY_NAMES
        for name, _ in parse_qsl(parsed.query)
    )


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
    if (
        not channel.stream_url
        or channel.removed
        or not channel.publishable_static
        or channel.requires_dynamic_resolution
        or channel.secret_refs
        or channel.delivery_mode in {"dai", "ssai"}
        or _has_sensitive_headers(channel.request_headers)
        or _has_sensitive_url(channel.stream_url)
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
