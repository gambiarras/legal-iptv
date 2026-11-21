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


class CompanionIntegrationTest(unittest.TestCase):
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
                    "--companion-video_platform-output", str(work / "video_platform.m3u"),
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
            self.assertEqual(metadata["video_platform_selected_channels"], 1)
            self.assertIn("plugin://client.resolver.example.invalid/play/?video_id=abcdefghijk",
                          (work / "video_platform.m3u").read_text())
            client.get_json.assert_not_called()
            client.get_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
