from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Channel:
    id: str
    name: str
    stream_url: str
    logo: str
    group: str
    source: str
    tvg_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    feed_id: str | None = None
    status: str | None = None
    resolved_at: str | None = None
    expires_at: str | None = None
    ttl_seconds: int | None = None
    provider_id: str | None = None
    logical_channel_id: str | None = None
    variant_id: str | None = None
    variant_label: str | None = None
    resolution: str | None = None
    codec: str | None = None
    bitrate: int | None = None
    protocol: str | None = None
    request_headers: dict[str, str] = field(default_factory=dict)
    secret_refs: dict[str, str] = field(default_factory=dict)
    requires_dynamic_resolution: bool = False
    publishable_static: bool = True
    delivery_mode: str = "direct"
    drm: dict[str, Any] | None = None
    removed: bool = False
    removal_reason: str | None = None
    raw_group: str | None = None
    is_adult: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
