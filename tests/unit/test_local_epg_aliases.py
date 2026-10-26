import copy
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from legal_iptv.services.epg_mapper import (
    EPGMapping,
    load_local_xmltv_aliases,
    parse_xmltv_aliases,
)


def make_guide(channel_id: str = "One.br", *, programmes: bool = True) -> bytes:
    programme = f'<programme channel="{channel_id}"/>' if programmes else ""
    return (
        f'<tv><channel id="{channel_id}">'
        f"<display-name>Fixture Channel</display-name></channel>"
        f"{programme}</tv>"
    ).encode("utf-8")


class LocalEPGAliasesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.guide = Path(directory.name) / "guide.xml"
        self.cache = self.guide.parent / ".cache" / "guide.xml.aliases.json"
        self.guide.write_bytes(make_guide())

    def test_reuses_identical_aliases_without_parsing_xml_again(self):
        expected = parse_xmltv_aliases(self.guide.read_bytes(), require_programmes=True)
        with patch(
            "legal_iptv.services.epg_mapper.parse_xmltv_aliases",
            wraps=parse_xmltv_aliases,
        ) as parser:
            cold = load_local_xmltv_aliases(self.guide)
            warm = load_local_xmltv_aliases(self.guide)

        self.assertEqual(cold, expected)
        self.assertEqual(warm, expected)
        self.assertEqual(parser.call_count, 1)
        self.assertTrue(self.cache.exists())

    def test_invalidates_changed_content_even_with_same_size_and_mtime(self):
        load_local_xmltv_aliases(self.guide)
        previous = self.guide.stat()
        self.guide.write_bytes(make_guide("Two.br"))
        os.utime(self.guide, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.assertEqual(self.guide.stat().st_size, previous.st_size)

        aliases = load_local_xmltv_aliases(self.guide)

        self.assertEqual(aliases["fixture channel"], EPGMapping("Two.br"))
        self.assertNotIn("one br", aliases)

    def test_invalidates_index_when_algorithm_version_changes(self):
        load_local_xmltv_aliases(self.guide)
        with patch("legal_iptv.services.epg_mapper.LOCAL_XMLTV_ALIASES_CACHE_VERSION", 2):
            with patch(
                "legal_iptv.services.epg_mapper.parse_xmltv_aliases",
                wraps=parse_xmltv_aliases,
            ) as parser:
                aliases = load_local_xmltv_aliases(self.guide)

        parser.assert_called_once()
        self.assertEqual(aliases["fixture channel"], EPGMapping("One.br"))
        self.assertEqual(json.loads(self.cache.read_text())["version"], 2)

    def test_rebuilds_corrupt_and_partial_indexes(self):
        expected = load_local_xmltv_aliases(self.guide)
        original = json.loads(self.cache.read_text())
        partial = copy.deepcopy(original)
        partial["aliases"]["fixture channel"] = {"display_name": "Missing ID"}
        cases = [b"{broken", b"\xff", b"[]", b"{}", json.dumps(partial).encode()]

        for payload in cases:
            with self.subTest(payload=payload):
                self.cache.write_bytes(payload)
                aliases = load_local_xmltv_aliases(self.guide)
                self.assertEqual(aliases, expected)
                self.assertEqual(json.loads(self.cache.read_text()), original)

    def test_does_not_reuse_an_index_without_programme_filtering(self):
        expected = load_local_xmltv_aliases(self.guide)
        payload = json.loads(self.cache.read_text())
        payload["require_programmes"] = False
        payload["aliases"]["unprogrammed channel"] = {"tvg_id": "Unprogrammed.br"}
        self.cache.write_text(json.dumps(payload))

        self.assertEqual(load_local_xmltv_aliases(self.guide), expected)

    def test_caches_empty_aliases_for_channels_without_programmes(self):
        self.guide.write_bytes(make_guide(programmes=False))
        with patch(
            "legal_iptv.services.epg_mapper.parse_xmltv_aliases",
            wraps=parse_xmltv_aliases,
        ) as parser:
            self.assertEqual(load_local_xmltv_aliases(self.guide), {})
            self.assertEqual(load_local_xmltv_aliases(self.guide), {})

        self.assertEqual(parser.call_count, 1)

    def test_returns_parsed_aliases_when_cache_cannot_be_written(self):
        with patch(
            "legal_iptv.services.epg_mapper.write_json_atomic",
            side_effect=PermissionError("Read-only cache directory"),
        ), self.assertLogs("legal_iptv.services.epg_mapper", level="WARNING"):
            aliases = load_local_xmltv_aliases(self.guide)

        self.assertEqual(aliases["fixture channel"], EPGMapping("One.br"))
        self.assertFalse(self.cache.exists())

    def test_returns_parsed_aliases_when_cache_path_is_not_a_file(self):
        self.cache.mkdir(parents=True)
        with self.assertLogs("legal_iptv.services.epg_mapper", level="WARNING"):
            aliases = load_local_xmltv_aliases(self.guide)

        self.assertEqual(aliases["fixture channel"], EPGMapping("One.br"))

    def test_does_not_reuse_index_when_guide_is_removed_or_invalid(self):
        load_local_xmltv_aliases(self.guide)
        self.guide.unlink()
        with self.assertRaises(FileNotFoundError):
            load_local_xmltv_aliases(self.guide)

        self.guide.write_bytes(b"<tv>invalid")
        with self.assertRaises(ET.ParseError):
            load_local_xmltv_aliases(self.guide)


if __name__ == "__main__":
    unittest.main()
