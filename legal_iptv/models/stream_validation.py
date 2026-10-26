from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class UrlValidation:
    active: bool | None
    http_status: int | None = None
    error_type: str | None = None
