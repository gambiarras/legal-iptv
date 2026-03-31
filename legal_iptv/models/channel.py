from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class Channel:
    id: str
    name: str
    stream_url: str
    logo: str
    group: str
    source: str
    source_type: str | None = None
    source_url: str | None = None
    status: str | None = None
    resolved_at: str | None = None
    expires_at: str | None = None
    ttl_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) 