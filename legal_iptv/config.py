from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class AppConfig:
    output_path: Path
    meta_output_path: Path
    log_level: str
    min_live_ttl: int
    live_catalog_file: Path | None
    validate_streams: bool
    validation_max_workers: int
    validation_timeout: int
    stream_status_file: Path
    stream_status_max_age: int
    epg_cache_file: Path
    epg_cache_ttl_seconds: int
    refresh_epg_cache: bool
    iptv_org_cache_file: Path
    iptv_org_cache_ttl_seconds: int
    refresh_iptv_org_cache: bool

    @classmethod
    def from_args(cls, args) -> "AppConfig":
        return cls(
            output_path=Path(args.output),
            meta_output_path=Path(args.meta_output),
            log_level=args.log_level.upper(),
            min_live_ttl=args.min_live_ttl,
            live_catalog_file=Path(args.live_catalog_file) if args.live_catalog_file else None,
            validate_streams=args.validate_streams,
            validation_max_workers=args.validation_max_workers,
            validation_timeout=args.validation_timeout,
            stream_status_file=Path(args.stream_status_file),
            stream_status_max_age=args.stream_status_max_age,
            epg_cache_file=Path(args.epg_cache_file),
            epg_cache_ttl_seconds=args.epg_cache_ttl,
            refresh_epg_cache=args.refresh_epg_cache,
            iptv_org_cache_file=Path(args.iptv_org_cache_file),
            iptv_org_cache_ttl_seconds=args.iptv_org_cache_ttl,
            refresh_iptv_org_cache=args.refresh_iptv_org_cache,
        )
