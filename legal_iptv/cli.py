import argparse


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")

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
    parser.add_argument("--validation-max-workers", type=positive_int, default=32)
    parser.add_argument("--validation-timeout", type=positive_int, default=6)
    parser.add_argument("--stream-status-file", default="stream-status.json")
    parser.add_argument("--stream-status-max-age", type=positive_int, default=14400)
    parser.add_argument("--epg-cache-file", default="epg-cache.json")
    parser.add_argument("--epg-cache-ttl", type=positive_int, default=43200)
    parser.add_argument("--refresh-epg-cache", action="store_true")
    parser.add_argument("--iptv-org-cache-file", default="iptv-org-cache.json")
    parser.add_argument("--iptv-org-cache-ttl", type=positive_int, default=43200)
    parser.add_argument("--refresh-iptv-org-cache", action="store_true")
    return parser.parse_args()
