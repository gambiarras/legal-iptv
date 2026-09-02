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
    profile: str = "base"
    player_profile: str = "portable"
    profile_config_file: Path | None = None
    guide_output_path: Path = Path("guide.xml")
    guide_url: str | None = None
    ensure_guide_enabled: bool = False
    guide_ttl_seconds: int = 18000
    guide_lkg_seconds: int = 172800
    guide_min_coverage_ratio: float = 0.5
    guide_diagnostics_file: Path = Path("guide-diagnostics.json")
    extra_removed_file: Path = Path("extra-removed.json")

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
            profile=args.profile,
            player_profile=args.player_profile,
            profile_config_file=(
                Path(args.profile_config_file)
                if args.profile_config_file
                else None
            ),
            guide_output_path=Path(args.guide_output),
            guide_url=args.guide_url,
            ensure_guide_enabled=not args.skip_guide,
            guide_ttl_seconds=args.guide_ttl,
            guide_lkg_seconds=args.guide_lkg,
            guide_min_coverage_ratio=args.guide_min_coverage_ratio,
            guide_diagnostics_file=Path(args.guide_diagnostics),
            extra_removed_file=Path(args.extra_removed_file),
        )
