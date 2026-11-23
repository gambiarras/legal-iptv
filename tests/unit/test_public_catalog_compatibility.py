"""Renamed private source labels must remain eligible when reusing old caches."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.sources.public_catalog import fetch_channels


class OfflineClient:
    def get_json(self, _url: str) -> None:
        raise RuntimeError("network unavailable")


class PublicCatalogCompatibilityTest(unittest.TestCase):
    def test_cached_source_aliases_remain_eligible_without_retiming(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            for generated_at, options in (
                ("2999-01-01T00:00:00+00:00", {}),
                ("2020-01-01T00:00:00+00:00", {}),
                ("2020-01-01T00:00:00+00:00", {"cache_only": True}),
            ):
                item = {
                    "id": "Example.br",
                    "name": "Example",
                    "stream_url": "https://media.example.test/live.m3u8",
                    "logo": "",
                    "group": "Variedades",
                    "tvg_id": "Example.br",
                    "source": "legacy_catalog",
                    "provider_id": "legacy_catalog",
                    "source_type": "legacy_catalog",
                    "protocol": "legacy_transport",
                }
                path.write_text(
                    json.dumps({"generated_at": generated_at, "channels": [item]})
                )
                before = (path.read_bytes(), path.stat().st_mtime_ns)
                aliases = {
                    "legacy_catalog": "public_catalog",
                    "legacy_transport": "hls",
                }
                with (
                    self.subTest(options=options, generated_at=generated_at),
                    patch(
                        "legal_iptv.sources.public_catalog.runtime_value",
                        return_value=aliases,
                    ),
                ):
                    channels = fetch_channels(
                        OfflineClient(), cache_file=path, **options
                    )
                self.assertEqual(len(channels), 1)
                channel = channels[0]
                self.assertEqual(
                    (
                        channel.source,
                        channel.provider_id,
                        channel.source_type,
                        channel.protocol,
                    ),
                    ("public_catalog", "public_catalog", "public_catalog", "hls"),
                )
                selected = select_profile_channels(
                    channels, load_playlist_configuration(), "full"
                )
                self.assertEqual([value.id for value in selected], [channel.id])
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
