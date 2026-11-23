"""Exact operator-reviewed public media rules; no global credential exception."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import SplitResult, parse_qsl, urlsplit

from legal_iptv.local_config import runtime_value
from legal_iptv.services.video_platform_transport import PRIVATE_NAMES

if TYPE_CHECKING:
    from legal_iptv.models import Channel


def checked_https(url: str) -> SplitResult | None:
    """Reject ambiguous authorities before comparing an operator-approved URL."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or "\\" in url
            or any(char.isspace() or ord(char) < 32 for char in url)
        ):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _matches_rule(channel: Channel, rule: dict[str, Any], parsed: SplitResult) -> bool:
    if (
        rule.get("channel_id") != channel.id
        or rule.get("source") != channel.source
        or rule.get("source_type") != channel.source_type
        or rule.get("host") != parsed.hostname
        or rule.get("path") != parsed.path
        or not parsed.path.endswith(".m3u8")
    ):
        return False
    parameters = rule.get("query_parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 8:
        return False
    values = parse_qsl(parsed.query, keep_blank_values=True)
    if len(values) != len(parameters) or {key for key, _ in values} != set(parameters):
        return False
    for key, value in values:
        constraint = parameters[key]
        if (
            key.strip().casefold() in (PRIVATE_NAMES | {"sig", "signature"}) - {"token"}
            or not isinstance(constraint, dict)
            or constraint.get("format") not in {"hex", "alphanumeric"}
            or type(constraint.get("length")) is not int
            or not 1 <= constraint["length"] <= 512
            or re.fullmatch(
                (
                    {"hex": r"[0-9a-fA-F]", "alphanumeric": r"[A-Za-z0-9]"}[
                        constraint["format"]
                    ]
                )
                + "{"
                + str(constraint["length"])
                + "}",
                value,
            )
            is None
        ):
            return False
    return True


def is_public_media_transport(channel: Channel) -> bool:
    """Authorize URL shape/origin only; aggregation separately requires sampled media."""
    if (
        channel.source != "extra"
        or channel.source_type != "curated_text"
        or channel.status != "resolved"
        or channel.removed
        or not channel.publishable_static
        or channel.requires_dynamic_resolution
        or channel.request_headers
        or channel.secret_refs
        or channel.drm is not None
        or channel.delivery_mode != "direct"
        or channel.protocol != "hls"
    ):
        return False
    parsed = checked_https(channel.stream_url)
    if parsed is None:
        return False
    rules = runtime_value("public_media_rules", [])
    return isinstance(rules, list) and any(
        isinstance(rule, dict) and _matches_rule(channel, rule, parsed)
        for rule in rules
    )
