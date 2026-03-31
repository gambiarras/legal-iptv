from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class AppConfig:
    output_path: Path
    meta_output_path: Path
    log_level: str
    min_live_ttl: int
    live_catalog_file: Path | None

    @classmethod
    def from_args(cls, args) -> "AppConfig":
        return cls(
            output_path=Path(args.output),
            meta_output_path=Path(args.meta_output),
            log_level=args.log_level.upper(),
            min_live_ttl=args.min_live_ttl,
            live_catalog_file=Path(args.live_catalog_file) if args.live_catalog_file else None,
        )