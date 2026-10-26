from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class UrlValidation:
    active: bool | None
    http_status: int | None = None
    error_type: str | None = None
    duration_ms: float | None = None
    request_count: int | None = None
