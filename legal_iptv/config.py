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
    public_catalog_cache_file: Path
    public_catalog_cache_ttl_seconds: int
    refresh_public_catalog_cache: bool
    validation_failure_threshold: int = 2
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
    validation_max_success_age: int = 86400
    validation_retry_delay: int = 900
    force_validation: bool = False
    companion_output_path: Path | None = None
    companion_meta_output_path: Path | None = None
    companion_guide_output_path: Path | None = None
    companion_guide_url: str | None = None
    companion_reference_file: Path | None = None
    companion_reference_cache_path: Path | None = None
    validation_due_only: bool = False
    require_media_proof: bool = False
    selection_proof_output_path: Path | None = None

    @classmethod
    def from_args(cls, args) -> "AppConfig":
        return cls(
            output_path=Path(args.output),
            meta_output_path=Path(args.meta_output),
            log_level=args.log_level.upper(),
            min_live_ttl=args.min_live_ttl,
            live_catalog_file=(
                Path(args.live_catalog_file) if args.live_catalog_file else None
            ),
            validate_streams=args.validate_streams,
            validation_max_workers=args.validation_max_workers,
            validation_timeout=args.validation_timeout,
            stream_status_file=Path(args.stream_status_file),
            stream_status_max_age=args.stream_status_max_age,
            epg_cache_file=Path(args.epg_cache_file),
            epg_cache_ttl_seconds=args.epg_cache_ttl,
            refresh_epg_cache=args.refresh_epg_cache,
            public_catalog_cache_file=Path(args.public_catalog_cache_file),
            public_catalog_cache_ttl_seconds=args.public_catalog_cache_ttl,
            refresh_public_catalog_cache=args.refresh_public_catalog_cache,
            validation_failure_threshold=args.validation_failure_threshold,
            profile=args.profile,
            player_profile=args.player_profile,
            profile_config_file=(
                Path(args.profile_config_file) if args.profile_config_file else None
            ),
            guide_output_path=Path(args.guide_output),
            guide_url=args.guide_url,
            ensure_guide_enabled=not args.skip_guide,
            guide_ttl_seconds=args.guide_ttl,
            guide_lkg_seconds=args.guide_lkg,
            guide_min_coverage_ratio=args.guide_min_coverage_ratio,
            guide_diagnostics_file=Path(args.guide_diagnostics),
            extra_removed_file=Path(args.extra_removed_file),
            validation_max_success_age=args.validation_max_success_age,
            validation_retry_delay=args.validation_retry_delay,
            force_validation=args.force_validation,
            companion_output_path=Path(args.companion_output) if args.companion_output else None,
            companion_meta_output_path=Path(args.companion_meta_output) if args.companion_meta_output else None,
            companion_guide_output_path=Path(args.companion_guide_output) if args.companion_guide_output else None,
            companion_guide_url=args.companion_guide_url,
            companion_reference_file=Path(args.companion_reference_file) if args.companion_reference_file else None,
            companion_reference_cache_path=Path(args.companion_reference_cache) if args.companion_reference_cache else None,
            validation_due_only=args.validation_due_only,
            require_media_proof=args.require_media_proof,
            selection_proof_output_path=Path(args.selection_proof_output) if args.selection_proof_output else None,
        )
