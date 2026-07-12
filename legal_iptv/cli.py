import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="legal-iptv",
        description="Aggregates public IPTV and live streaming catalogs into a single M3U playlist",
    )
    parser.add_argument("--output", default="playlist.m3u")
    parser.add_argument("--meta-output", default="playlist.meta.json")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--min-live-ttl", type=int, default=900)
    parser.add_argument("--live-catalog-file", default=None)
    parser.add_argument("--validate-streams", action="store_true")
    parser.add_argument("--validation-max-workers", type=int, default=32)
    parser.add_argument("--validation-timeout", type=int, default=6)
    parser.add_argument("--stream-status-file", default="stream-status.json")
    parser.add_argument("--stream-status-max-age", type=int, default=14400)
    return parser.parse_args()
