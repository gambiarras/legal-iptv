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
    return parser.parse_args()