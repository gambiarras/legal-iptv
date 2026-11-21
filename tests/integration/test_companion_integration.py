import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.__main__ import main
from legal_iptv.models import Channel
from legal_iptv.models.stream_validation import UrlValidation


class CompanionIntegrationTest(unittest.TestCase):
    def test_resolved_public_video_platform_hls_passes_media_proof_into_both_regular_lists(self):
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(hours=4)
        url = ("https://manifest.media-video.example.invalid/api/manifest/hls_variant/expire/"
               f"{int(expiry.timestamp())}/sig/public-media/index.m3u8")
        for observation, expected_count in ((UrlValidation(True, 200), 1), (UrlValidation(None, 503), 0)):
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                (work / "guide.xml").write_text("<tv/>")
                (work / "reference.json").write_text(json.dumps({
                    "version": 3, "generated_at": now.isoformat(),
                    "base_reference_channels": [f"Other Channel {index}" for index in range(25)],
                    "directory_catalog_channels": [],
                }))
                (work / "catalog.json").write_text(json.dumps([{
                    "id": "official.abcdefghijk", "name": "Official Live - Daily live",
                    "source_type": "video_platform", "provider_id": "video_platform", "group": "news",
                    "source_url": "https://www.video-platform.example.invalid/watch?v=abcdefghijk",
                    "stream_url": url, "status": "resolved", "protocol": "hls",
                    "resolved_at": now.isoformat(), "expires_at": expiry.isoformat(),
                    "requires_dynamic_resolution": True, "publishable_static": False,
                }]))
                argv = ["legal-iptv", "--validate-streams", "--skip-guide",
                        "--output", str(work / "main.m3u"),
                        "--meta-output", str(work / "main.meta.json"),
                        "--guide-output", str(work / "guide.xml"),
                        "--live-catalog-file", str(work / "catalog.json"),
                        "--stream-status-file", str(work / "status.json"),
                        "--companion-output", str(work / "companion.m3u"),
                        "--companion-meta-output", str(work / "companion.meta.json"),
                        "--companion-guide-output", str(work / "companion-guide.xml"),
                        "--companion-reference-file", str(work / "reference.json")]
                client = Mock()
                with patch.object(sys, "argv", argv), \
                     patch("legal_iptv.services.aggregate.HttpClient", return_value=client), \
                     patch("legal_iptv.services.aggregate.extra_channels.fetch_channels", return_value=[]), \
                     patch("legal_iptv.services.aggregate.public_catalog.fetch_channels", return_value=[]), \
                     patch("legal_iptv.services.aggregate.load_xmltv_aliases", return_value={}), \
                     patch("legal_iptv.services.link_validator.inspect_url",
                           return_value=observation) as probe:
                    main()
                probe.assert_called_once()
                for stem in ("main", "companion"):
                    playlist = (work / f"{stem}.m3u").read_text()
                    metadata = json.loads((work / f"{stem}.meta.json").read_text())
                    self.assertEqual(url in playlist, bool(expected_count))
                    self.assertEqual(playlist.count("#EXTINF:"), expected_count)
                    self.assertEqual(metadata["selected_channels"], expected_count)
                    self.assertNotIn("plugin://", playlist)
                self.assertTrue(json.loads((work / "main.meta.json").read_text())["media_proof_required"])
                self.assertEqual((work / "main.m3u").read_text().splitlines()[0],
                                 (work / "companion.m3u").read_text().splitlines()[0])
                client.get_json.assert_not_called()
                client.get_text.assert_not_called()

    def test_real_cli_generates_matching_playlists_with_one_shared_guide(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            guide = ET.Element("tv")
            entry = ET.SubElement(guide, "channel", id="ExampleChannel053.br")
            ET.SubElement(entry, "display-name").text = "Required Channel"
            for start, stop in [(-1, 1), (1, 2)]:
                item = ET.SubElement(guide, "programme", channel="ExampleChannel053.br",
                    start=(now + timedelta(hours=start)).strftime("%Y%m%d%H%M%S %z"),
                    stop=(now + timedelta(hours=stop)).strftime("%Y%m%d%H%M%S %z"))
                ET.SubElement(item, "title").text = "Declared programme"
            ET.ElementTree(guide).write(work / "guide.xml", encoding="utf-8")
            reference = {
                "version": 2, "generated_at": now.isoformat(),
                "base_reference_channels": ["Sports Example", "BroadcastExample"] + [f"Base Reference {i}" for i in range(25)],
                "directory_catalog_channels": [f"Directory {i}" for i in range(25)],
            }
            (work / "reference.json").write_text(json.dumps(reference))
            (work / "catalog.json").write_text(json.dumps([{
                "id": "bean", "name": "Comedy Example - Live", "source_type": "video_platform",
                "source_url": "https://www.video-platform.example.invalid/watch?v=abcdefghijk",
                "resolved_at": now.isoformat(), "status": "resolved",
            }]))
            candidates = [Channel(id="ExampleChannel124.br", name="Required Channel", tvg_id="ExampleChannel053.br",
                                 stream_url="https://fox.example.test/live.m3u8", logo="",
                                 group="Infantil", source="extra"),
                          Channel(id="ExampleChannel006", name="BroadcastExample", tvg_id="ExampleChannel006.br",
                                 stream_url="https://ExampleChannel006.example.test/live.m3u8", logo="",
                                 group="TV Aberta", source="extra")]
            argv = ["legal-iptv", "--no-require-media-proof", "--output", str(work / "main.m3u"),
                    "--meta-output", str(work / "main.meta.json"),
                    "--guide-output", str(work / "guide.xml"),
                    "--live-catalog-file", str(work / "catalog.json"),
                    "--stream-status-file", str(work / "status.json"),
                    "--companion-output", str(work / "companion.m3u"),
                    "--companion-meta-output", str(work / "companion.meta.json"),
                    "--companion-guide-output", str(work / "companion-guide.xml"),
                    "--companion-reference-file", str(work / "reference.json")]
            client = Mock()
            with patch.object(sys, "argv", argv), \
                 patch("legal_iptv.services.aggregate.HttpClient", return_value=client), \
                 patch("legal_iptv.services.aggregate.extra_channels.fetch_channels", return_value=candidates), \
                 patch("legal_iptv.services.aggregate.public_catalog.fetch_channels", return_value=[]), \
                 patch("legal_iptv.services.aggregate.live_stream_catalog.fetch_channels", return_value=[]), \
                 patch("legal_iptv.services.aggregate.load_xmltv_aliases", return_value={}):
                main()
            full = (work / "main.m3u").read_text()
            companion = (work / "companion.m3u").read_text()
            self.assertIn("BroadcastExample", full)
            self.assertNotIn("BroadcastExample", companion)
            self.assertIn('tvg-id="ExampleChannel053.br"', companion)
            self.assertEqual(full.splitlines()[0], companion.splitlines()[0])
            self.assertIn("https://our.example.invalid/guide.xml,https://primary-epg-url.example.invalid/", companion.splitlines()[0])
            self.assertEqual((work / "guide.xml").read_bytes(), (work / "companion-guide.xml").read_bytes())
            root = ET.parse(work / "companion-guide.xml").getroot()
            self.assertEqual({item.get("channel") for item in root.findall("programme")}, {"ExampleChannel053.br"})
            metadata = json.loads((work / "companion.meta.json").read_text())
            self.assertEqual(metadata["selected_channels"], 1)
            self.assertNotIn("video_platform_selected_channels", metadata)
            self.assertNotIn("plugin://", companion)
            client.get_json.assert_not_called()
            client.get_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
