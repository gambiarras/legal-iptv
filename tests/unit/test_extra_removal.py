import json
import tempfile
import unittest
from pathlib import Path

from legal_iptv.models import Channel
from legal_iptv.models.stream_validation import UrlValidation
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

    def test_omitted_channel_and_success_status_alone_do_not_clear_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "removed.json"
            channel = extra_channel("https://example.test/missing.m3u8")
            update_extra_removals([channel], {channel.stream_url: 404}, path)
            original = json.loads(path.read_text())["channels"]
            update_extra_removals([], {}, path)
            self.assertEqual(json.loads(path.read_text())["channels"], original)
            update_extra_removals([channel], {channel.stream_url: 200}, path)
            self.assertEqual(json.loads(path.read_text())["channels"], original)
            update_extra_removals([channel], {}, path, validation_by_channel={channel.id: UrlValidation(False, 200, "InvalidMediaContent")})
            self.assertEqual(json.loads(path.read_text())["channels"], original)
            update_extra_removals([channel], {}, path, validation_by_channel={channel.id: UrlValidation(True, 200)})
            self.assertEqual(json.loads(path.read_text())["channels"], {})

    def test_missing_segment_does_not_tombstone_live_playlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "removed.json"
            channel = extra_channel("https://example.test/live.m3u8")
            update_extra_removals([channel], {}, path, validation_by_channel={channel.id: UrlValidation(False, 404, "MediaNotFound")})
            self.assertEqual(json.loads(path.read_text())["channels"], {})

    def test_storage_budget_preserves_previous_file_on_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "removed.json"
            original = json.dumps({"channels": {str(index): {"url": "https://example.test/old", "reason": "http_404"} for index in range(5000)}})
            path.write_text(original)
            channel = extra_channel("https://example.test/new")
            with self.assertRaisesRegex(ValueError, "budget"):
                update_extra_removals([channel], {channel.stream_url: 404}, path)
            self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
