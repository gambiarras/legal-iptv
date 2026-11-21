import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from legal_iptv.services.guide import (
    ensure_guide,
    merge_provider_guides,
    validate_guide_root,
)


def xmltv_root(channel_count: int = 1, programmes_per_channel: int = 2) -> ET.Element:
    now = datetime.now(timezone.utc)
    root = ET.Element("tv", {"generator-info-name": "upstream"})
    for index in range(channel_count):
        channel_id = f"channel-{index}"
        channel = ET.SubElement(root, "channel", {"id": channel_id})
        ET.SubElement(channel, "display-name").text = f"Channel {index}"
        windows = [
            (now - timedelta(hours=1), now + timedelta(hours=1)),
            (now + timedelta(hours=1), now + timedelta(hours=2)),
        ]
        for programme_index, (start, stop) in enumerate(windows[:programmes_per_channel]):
            programme = ET.SubElement(
                root,
                "programme",
                {
                    "channel": channel_id,
                    "start": start.strftime("%Y%m%d%H%M%S %z"),
                    "stop": stop.strftime("%Y%m%d%H%M%S %z"),
                },
            )
            ET.SubElement(programme, "title").text = f"Programme {programme_index}"
    return root


class GuideTest(unittest.TestCase):
    def test_validates_present_and_future_coverage(self):
        stats = validate_guide_root(xmltv_root())

        self.assertEqual(stats.channels, 1)
        self.assertEqual(stats.programmes, 2)
        self.assertEqual(stats.present_programmes, 1)
        self.assertEqual(stats.future_programmes, 1)

    def test_merges_provider_programmes_with_neutral_generator(self):
        first = xmltv_root()
        second = xmltv_root()
        second.find("channel/display-name").text = "Lower priority name"
        second.findall("programme")[1].find("title").text = "Additional programme"

        merged = merge_provider_guides([first, second])

        self.assertEqual(merged.attrib["generator-info-name"], "local-xmltv-generator")
        self.assertEqual(merged.findtext("channel/display-name"), "Channel 0")
        self.assertEqual(len(merged.findall("programme")), 2)
        self.assertEqual(
            merged.findall("programme")[1].findtext("title"),
            "Programme 1",
        )

    def test_rejects_abnormal_drop_and_keeps_recent_last_known_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide_path = root / "guide.xml"
            diagnostics_path = root / "guide-diagnostics.json"
            old_root = xmltv_root(channel_count=2)
            ET.ElementTree(old_root).write(guide_path, encoding="utf-8", xml_declaration=True)
            previous = guide_path.read_bytes()
            old_timestamp = (datetime.now(timezone.utc) - timedelta(seconds=2)).timestamp()
            os.utime(guide_path, (old_timestamp, old_timestamp))
            provider = Mock()
            provider.id = "primary"
            provider.fetch.return_value = xmltv_root(channel_count=1)

            result = ensure_guide(
                Mock(),
                output_path=guide_path,
                diagnostics_path=diagnostics_path,
                ttl_seconds=1,
                lkg_seconds=172800,
                min_coverage_ratio=0.75,
                providers=[provider],
            )

            self.assertEqual(result.status, "retained")
            self.assertEqual(guide_path.read_bytes(), previous)
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            self.assertEqual(diagnostics["status"], "retained")
            self.assertNotIn("url", str(diagnostics).casefold())

    def test_writes_valid_candidate_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide_path = root / "guide.xml"
            provider = Mock()
            provider.id = "primary"
            provider.fetch.return_value = xmltv_root()

            result = ensure_guide(
                Mock(),
                output_path=guide_path,
                diagnostics_path=root / "diagnostics.json",
                ttl_seconds=18000,
                lkg_seconds=172800,
                min_coverage_ratio=0.5,
                providers=[provider],
            )

            self.assertEqual(result.status, "updated")
            self.assertEqual(ET.parse(guide_path).getroot().tag, "tv")
            self.assertEqual(
                ET.parse(guide_path).getroot().attrib["generator-info-name"],
                "local-xmltv-generator",
            )


if __name__ == "__main__":
    unittest.main()
