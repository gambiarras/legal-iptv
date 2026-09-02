import json
import tempfile
import unittest
from pathlib import Path

from legal_iptv.models import Channel
from legal_iptv.services.extra_removal import update_extra_removals


def extra_channel(url: str) -> Channel:
    return Channel(
        id="manual.channel",
        name="Manual",
        stream_url=url,
        logo="",
        group="Variedades",
        source="extra",
    )


class ExtraRemovalTest(unittest.TestCase):
    def test_persists_only_definitive_404_or_410(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extra-removed.json"
            channel = extra_channel("https://example.test/missing.m3u8")

            update_extra_removals([channel], {channel.stream_url: 404}, path)
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["channels"][channel.id]["reason"], "http_404")

    def test_changed_url_clears_the_old_tombstone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extra-removed.json"
            old = extra_channel("https://example.test/old.m3u8")
            update_extra_removals([old], {old.stream_url: 410}, path)
            changed = extra_channel("https://example.test/new.m3u8")

            update_extra_removals([changed], {changed.stream_url: 200}, path)
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["channels"], {})

    def test_transient_failure_does_not_create_tombstone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extra-removed.json"
            channel = extra_channel("https://example.test/error.m3u8")

            update_extra_removals([channel], {channel.stream_url: 503}, path)
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["channels"], {})


if __name__ == "__main__":
    unittest.main()
