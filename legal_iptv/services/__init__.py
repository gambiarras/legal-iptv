"""Service entry point, loaded lazily to avoid exporter/service import cycles."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from legal_iptv.config import AppConfig

__all__ = ["run_aggregation"]


def run_aggregation(config: "AppConfig") -> None:
    from legal_iptv.services.aggregate import run_aggregation as aggregate

    aggregate(config)
