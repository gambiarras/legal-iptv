import json
import tempfile
import unittest
from pathlib import Path

from legal_iptv.sources.extra_channels import fetch_channels


class ExtraChannelsSourceTest(unittest.TestCase):
    def test_loads_packaged_extra_channels_resource(self):
        channels = fetch_channels()
        channels_by_id = {channel.id: channel for channel in channels}

        self.assertGreater(len(channels), 0)
        self.assertTrue(all(channel.source == "extra" for channel in channels))
        self.assertTrue(all(channel.stream_url for channel in channels))
        self.assertEqual(channels_by_id["CNNBrasil.br"].tvg_id, "CNNBrasil.br")
        self.assertEqual(channels_by_id["MyTimemovienetworkBrazil.br"].tvg_id, "MyTimeMovieNetwork.br")
        self.assertEqual(channels_by_id["AniTV.br"].tvg_id, "Geekdot.br")
        self.assertIsNone(channels_by_id["RunTime.br"].tvg_id)

    def test_marks_matching_persisted_url_as_removed(self):
        original = fetch_channels()[0]
        with tempfile.TemporaryDirectory() as directory:
            removed_file = Path(directory) / "extra-removed.json"
            removed_file.write_text(
                json.dumps(
                    {
                        "channels": {
                            original.id: {
                                "url": original.stream_url,
                                "reason": "http_404",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            channels = fetch_channels(removed_file)

        removed = next(channel for channel in channels if channel.id == original.id)
        self.assertTrue(removed.removed)
        self.assertEqual(removed.status, "removed")
        self.assertEqual(removed.removal_reason, "http_404")


if __name__ == "__main__":
    unittest.main()
