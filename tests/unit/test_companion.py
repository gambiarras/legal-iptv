import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel
from legal_iptv.services.companion import (
    build_companion_guide, enrich_companion_candidates, select_companion_channels, youtube_channels,
)
from legal_iptv.services.epg_mapper import EPGMapping
from legal_iptv.services.companion_reference import (
    build_reference, identity_key, load_reference, validate_reference,
)


def channel(name="Fox Kids", **kwargs):
    values = dict(id="fkids.br", name=name, stream_url="https://media.example.test/live.m3u8",
                  logo="", group="Infantil", source="extra", tvg_id="FoxKids.br")
    values.update(kwargs)
    return Channel(**values)


def reference(**kwargs):
    values = dict(version=2, generated_at=datetime.now(timezone.utc).isoformat(),
                  saimo_channels=["CazéTV", "Globo SP", "Canal HD"],
                  televisao_channels=["TV Pública", "Canal", "Globo RJ", "Globo SP"])
    values.update(kwargs)
    return values


class CompanionSelectionTest(unittest.TestCase):
    def test_epg_enrichment_cannot_hide_alternatives_or_regional_identity(self):
        values = [channel("TV Pública Alternativo", id="alt", tvg_id="Publica.br", provider_id="rest_catalog_1"),
                  channel("Globo RJ", id="rj", tvg_id="Globo.br", source="iptv_org")]
        aliases = {"publica br": EPGMapping("Publica.br", "TV Pública"),
                   "globo br": EPGMapping("Globo.br", "Globo")}
        enriched = enrich_companion_candidates(values, aliases)
        self.assertEqual([item.name for item in enriched], ["TV Pública Alternativo", "Globo RJ"])
        selected, excluded = select_companion_channels(enriched, reference())
        self.assertEqual([item.id for item in selected], ["rj"])
        self.assertEqual(excluded[0]["reason"], "alternative")

    def test_excludes_saimo_identity_without_collapsing_regional_feeds(self):
        values = [channel("Globo SP [FHD]", id="sp", source="iptv_org"),
                  channel("Globo RJ", id="rj", source="iptv_org"),
                  channel("Canal SD", id="canal", source="iptv_org")]
        selected, excluded = select_companion_channels(values, reference())
        self.assertEqual([item.id for item in selected], ["rj"])
        self.assertEqual([row["reason"] for row in excluded], ["already_in_saimo"] * 2)

    def test_keeps_fox_and_retro_even_when_not_in_directory(self):
        values = [channel(), channel("Retrô Cartoon", id="RetroCartoon.br", tvg_id="RetroCartoon.br",
                                     stream_url="https://retro.example.test/live.m3u8")]
        selected, _ = select_companion_channels(values, reference())
        self.assertEqual({item.tvg_id for item in selected}, {"FoxKids.br", "RetroCartoon.br"})

    def test_rejects_paid_alternative_frost_but_does_not_require_directory_membership(self):
        values = [channel("HBO Mundi", provider_id="rest_catalog_1"),
                  channel("TV Pública Alternativo", provider_id="rest_catalog_1"),
                  channel("Fox Kids", provider_id="addon_catalog_1"),
                  channel("TV Pública", stream_url="https://frostview.example.test/live.m3u8"),
                  channel("Unreviewed", id="other", provider_id="rest_catalog_1")]
        values[-1].source = "live_stream_catalog"
        selected, excluded = select_companion_channels(values, reference())
        self.assertEqual([item.id for item in selected], ["other"])
        self.assertEqual([row["reason"] for row in excluded], [
            "paid_channel", "alternative", "excluded_provider", "excluded_provider"])

    def test_deduplicates_quality_then_prefers_batista_for_reviewed_public_channel(self):
        values = [channel("TV Pública", id="one", source="iptv_org", resolution="1080p"),
                  channel("TV Pública HD", id="two", source="live_stream_catalog",
                          provider_id="rest_catalog_1", resolution="720p",
                          stream_url="https://batista.example.test/live.m3u8"),
                  channel("TV Pública FHD", id="three", source="live_stream_catalog",
                          provider_id="rest_catalog_1", resolution="1080p",
                          stream_url="https://batista.example.test/full.m3u8")]
        selected, _ = select_companion_channels(values, reference())
        self.assertEqual([item.id for item in selected], ["three"])

    def test_signed_dynamic_html_and_account_paths_are_not_portable(self):
        values = [channel(stream_url="https://media.example.test/live.m3u8?token=private"),
                  channel(requires_dynamic_resolution=True),
                  channel(stream_url="https://www.youtube.com/watch?v=abcdefghijk"),
                  channel(stream_url="https://media.example.test/live/user/password/1.m3u8"),
                  channel(stream_url="https://media.example.test/get.php?username=account&password=secret"),
                  channel(stream_url="https://media.example.test/live.m3u8?apikey=secret"),
                  channel(stream_url="https://media.example.test/player.html"),
                  channel(stream_url="https://[invalid/live.m3u8"),
                  channel(protocol="embed")]
        selected, excluded = select_companion_channels(values, reference())
        self.assertFalse(selected)
        self.assertNotIn("password", json.dumps(excluded))
        self.assertNotIn("private", json.dumps(excluded))

    def test_canonical_guide_is_the_only_header_reference(self):
        result = render_m3u([channel()], guide_url="https://example.test/companion-guide.xml",
                            include_primary_guide=False, legacy_guide_urls=False)
        self.assertNotIn("legal-epg", result)
        self.assertIn('x-tvg-url="https://example.test/companion-guide.xml"', result)


class CompanionGuideTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    def guide(self, path, *, offset=0):
        root = ET.Element("tv")
        for cid in ["FoxKids.br", "Unrelated.br"]:
            declaration = ET.SubElement(root, "channel", id=cid)
            ET.SubElement(declaration, "display-name").text = cid
            for start_hours, stop_hours in [(-3, -2), (-1, 1), (1, 2)]:
                programme = ET.SubElement(root, "programme", channel=cid,
                    start=(self.now + timedelta(hours=start_hours + offset)).strftime("%Y%m%d%H%M%S %z"),
                    stop=(self.now + timedelta(hours=stop_hours + offset)).strftime("%Y%m%d%H%M%S %z"))
                ET.SubElement(programme, "title").text = "Actual source title"
        ET.ElementTree(root).write(path, encoding="utf-8")

    def test_subsets_guide_and_preserves_exact_ids_and_source_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "guide.xml"
            self.guide(path)
            root, stats = build_companion_guide(path, [channel()], now=self.now)
            self.assertEqual([item.get("id") for item in root.findall("channel")], ["FoxKids.br"])
            self.assertEqual(len(root.findall("programme")), 2)
            self.assertTrue(stats["FoxKids.br"]["current"])
            self.assertTrue(stats["FoxKids.br"]["future"])
            self.assertTrue(all(item.get("channel") == "FoxKids.br" for item in root.findall("programme")))

    def test_rejects_expired_required_channel_even_if_other_guide_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "guide.xml"
            self.guide(path, offset=-24)
            with self.assertRaisesRegex(ValueError, "coverage"):
                build_companion_guide(path, [channel()], now=self.now)

    def test_required_channel_cannot_be_silently_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "guide.xml"
            self.guide(path)
            with self.assertRaisesRegex(ValueError, "absent"):
                build_companion_guide(path, [], now=self.now)


class CompanionReferenceTest(unittest.TestCase):
    def test_saimo_reference_does_not_depend_on_the_televisao_directory(self):
        catalogue = "\n".join(f"canal: Base {i}\nfonte: https://media.example.test/{i}" for i in range(25))
        client = Mock()
        client.get_text.side_effect = [catalogue, "#EXTM3U"]
        client.get_json.return_value = {"desativados": []}
        with tempfile.TemporaryDirectory() as directory:
            reference_data = load_reference(client, Path(directory) / "reference.json")
        self.assertEqual(reference_data["version"], 3)
        self.assertEqual(len(reference_data["saimo_channels"]), 25)
        self.assertEqual(reference_data["televisao_channels"], [])
        self.assertEqual(client.get_json.call_count, 1)
        self.assertNotIn("channels-data.php", json.dumps(reference_data))

    def test_android_extras_use_tvg_id_and_preserve_distinct_base_records(self):
        catalogue = "\n".join(f"canal: Base {i}\nfonte: https://media.example.test/{i}.m3u8" for i in range(25))
        catalogue += "\ncanal: Rede TV!\nfonte: https://a.example.test/live.m3u8"
        catalogue += "\ncanal: RedeTV!\nfonte: https://b.example.test/live.m3u8"
        extras = ('#EXTM3U\n#EXTINF:-1 tvg-id="Base 1",Base 1 (S79)\nhttps://extra.example.test/1.m3u8\n'
                  '#EXTINF:-1,Base 2 (S79)\nhttps://extra.example.test/2.m3u8\n')
        directory = {"ok": True, "channels": [{"name": f"TV {i}"} for i in range(25)]}
        data = build_reference(catalogue, extras, {"desativados": []}, directory)
        self.assertEqual(len(data["saimo_channels"]), 27)
        self.assertIn("Rede TV!", data["saimo_channels"])
        self.assertIn("RedeTV!", data["saimo_channels"])
        self.assertFalse(any("S79" in name for name in data["saimo_channels"]))

    def test_parses_filter_and_extras_without_caching_sensitive_streams(self):
        catalogue = "\n".join(f"canal: Channel {i}\nfonte: https://media.example.test/live/user/secret/{i}.m3u8" for i in range(25))
        catalogue += "\ncanal: Disabled\nfonte: https://disabled.example.test/live.m3u8"
        extras = '#EXTM3U\n#EXTINF:-1 group-title="a,b",Extra\nhttps://extra.example.test/live.m3u8\n'
        directory = {"ok": True, "channels": [{"name": f"TV {i}"} for i in range(25)]}
        data = build_reference(catalogue, extras, {"desativados": ["disabled.example.test"]}, directory)
        self.assertNotIn("Disabled", data["saimo_channels"])
        self.assertIn("Extra", data["saimo_channels"])
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn("media.example.test", json.dumps(data))
        self.assertEqual(identity_key("Retrô Cartoon [FHD]"), "retrocartoon")

    def test_fails_closed_on_first_reference_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Mock()
            client.get_text.side_effect = RuntimeError("private URL")
            with self.assertRaisesRegex(RuntimeError, "refusing"):
                load_reference(client, Path(tmp) / "reference.json")

    def test_nonobject_cache_is_rejected_and_refetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.json"
            path.write_text("[]")
            client = Mock()
            client.get_text.side_effect = RuntimeError("offline")
            with self.assertRaisesRegex(RuntimeError, "refusing"):
                load_reference(client, path)
            client.get_text.assert_called_once()

    def test_retains_valid_reference_without_refreshing_its_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.json"
            data = reference(saimo_channels=[f"Saimo {i}" for i in range(25)],
                             televisao_channels=[f"TV {i}" for i in range(25)],
                             generated_at=(datetime.now(timezone.utc) - timedelta(hours=12)).isoformat())
            path.write_text(json.dumps(data))
            client = Mock()
            client.get_text.side_effect = RuntimeError("offline")
            retained = load_reference(client, path)
            self.assertTrue(retained["retained_reference"])
            self.assertEqual(retained["generated_at"], data["generated_at"])
            data["generated_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            with self.assertRaises(ValueError):
                validate_reference(data)


class CompanionYouTubeTest(unittest.TestCase):
    def test_uses_public_video_id_in_client_resolver_without_media_secrets(self):
        now = datetime.now(timezone.utc)
        base = dict(source_type="youtube", status="resolved", resolved_at=now.isoformat(),
                    stream_url="https://private.example.test/video?token=secret")
        raw = [{**base, "id": "caze.abcdefghijk", "name": "CazéTV - Live", "source_url": "https://youtube.com/watch?v=abcdefghijk"},
               {**base, "id": "bean.abcdefghijk", "logical_channel_id": "bean", "name": "Mr Bean - Live", "source_url": "https://youtube.com/watch?v=abcdefghijk"},
               {**base, "id": "bean.12345678901", "logical_channel_id": "bean", "name": "Mr Bean - Other Live", "source_url": "https://youtube.com/watch?v=12345678901"},
               {**base, "id": "old", "name": "Old", "resolved_at": (now - timedelta(days=2)).isoformat(), "source_url": "https://youtube.com/watch?v=abcdefghijk"}]
        selected, skipped = youtube_channels(raw, reference(), now=now)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].stream_url, "plugin://plugin.video.youtube/play/?video_id=abcdefghijk")
        self.assertNotIn("secret", json.dumps(skipped))
        self.assertNotIn("Cazé", str(selected))


if __name__ == "__main__":
    unittest.main()
