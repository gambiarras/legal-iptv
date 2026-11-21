import argparse
from pathlib import Path


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")

    return parsed


def coverage_ratio(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed <= 1:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 1")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="legal-iptv",
        description="Aggregates public IPTV and live streaming catalogs into a single M3U playlist",
    )
    parser.add_argument("--output", default="playlist.m3u")
    parser.add_argument("--meta-output", default="playlist.meta.json")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--min-live-ttl", type=positive_int, default=900)
    parser.add_argument("--live-catalog-file", default=None)
    parser.add_argument("--validate-streams", action="store_true")
    parser.add_argument("--require-media-proof", action=argparse.BooleanOptionalAction, default=True,
                        help="Publish only transports with recent media sampling evidence (default)")
    parser.add_argument("--validation-due-only", action="store_true",
                        help="Validate due transports using existing catalog, source and EPG caches only")
    parser.add_argument(
        "--force-validation",
        action="store_true",
        help="Check all eligible transports; requires --validate-streams",
    )
    parser.add_argument(
        "--validation-max-success-age",
        type=positive_int,
        default=86400,
        help="Maximum reuse interval after consistent HTTP successes (seconds)",
    )
    parser.add_argument(
        "--validation-retry-delay",
        type=positive_int,
        default=900,
        help="Initial retry interval for offline/unknown observations (seconds)",
    )
    parser.add_argument("--validation-max-workers", type=positive_int, default=32)
    parser.add_argument("--validation-timeout", type=positive_int, default=6)
    parser.add_argument("--validation-failure-threshold", type=positive_int, default=2)
    parser.add_argument("--stream-status-file", default="stream-status.json")
    parser.add_argument("--stream-status-max-age", type=positive_int, default=14400)
    parser.add_argument("--epg-cache-file", default="epg-cache.json")
    parser.add_argument("--epg-cache-ttl", type=positive_int, default=43200)
    parser.add_argument("--refresh-epg-cache", action="store_true")
    parser.add_argument("--public-catalog-cache-file", default="public-catalog-cache.json")
    parser.add_argument("--public-catalog-cache-ttl", type=positive_int, default=43200)
    parser.add_argument("--refresh-public-catalog-cache", action="store_true")
    parser.add_argument("--profile", choices=("base", "full"), default="full")
    parser.add_argument(
        "--player-profile",
        choices=("portable", "kodi"),
        default="portable",
    )
    parser.add_argument("--profile-config-file", default=None)
    parser.add_argument("--guide-output", default="guide.xml")
    parser.add_argument("--guide-url", default=None)
    parser.add_argument("--skip-guide", action="store_true")
    parser.add_argument("--guide-ttl", type=positive_int, default=18000)
    parser.add_argument("--guide-lkg", type=positive_int, default=172800)
    parser.add_argument("--guide-min-coverage-ratio", type=coverage_ratio, default=0.5)
    parser.add_argument("--guide-diagnostics", default="guide-diagnostics.json")
    parser.add_argument("--extra-removed-file", default="extra-removed.json")
    parser.add_argument("--companion-output", default=None)
    parser.add_argument("--companion-meta-output", default=None)
    parser.add_argument("--companion-guide-output", default=None)
    parser.add_argument("--companion-guide-url", default=None)
    parser.add_argument("--companion-reference-file", default=None,
                        help="Sanitized Base Reference/Directory Catalog reference snapshot for offline generation")
    parser.add_argument("--companion-reference-cache", default=None)
    args = parser.parse_args()
    if args.validation_due_only:
        args.validate_streams = True
        args.skip_guide = True
        if not args.live_catalog_file:
            parser.error("--validation-due-only requires --live-catalog-file")
        if args.force_validation or args.refresh_epg_cache or args.refresh_public_catalog_cache:
            parser.error("--validation-due-only cannot force checks or refresh caches")
    if args.force_validation and not args.validate_streams:
        parser.error("--force-validation requires --validate-streams")
    companion_paths = (args.companion_output, args.companion_meta_output, args.companion_guide_output)
    if any(companion_paths) and not all(companion_paths):
        parser.error("Companion requires --companion-output, --companion-meta-output and --companion-guide-output")
    outputs = [args.output, args.meta_output, args.guide_output,
               *[path for path in companion_paths if path]]
    if len({Path(path).resolve() for path in outputs}) != len(outputs):
        parser.error("Output paths must be distinct")
    return args
