import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.services.epg_collectors import (
    CHANNEL_SCHEDULE_CATALOG,
    CHANNEL_SCHEDULE_ID,
    CHANNEL_SCHEDULE_XMLTV,
    cached_payload,
    fetch_channel_schedule,
    fetch_html_schedules,
    channel_schedule_catalog_to_xmltv,
    has_coverage,
    parse_schedule_site_a_html,
    parse_schedule_site_b_html,
)
from legal_iptv.services.epg_providers import EpgProvider, load_epg_providers
from legal_iptv.services.guide import ensure_guide, parse_xmltv_time, validate_guide_root

FIXTURES = Path(__file__).parents[1] / "fixtures" / "epg"
NOW = datetime(2026, 11, 14, 15, tzinfo=timezone.utc)  # noon in Sao Paulo


def fixture(name):
    return (FIXTURES / name).read_bytes()


class EpgCollectorsTest(unittest.TestCase):
    def test_channel_schedule_expands_declared_dates_only_with_block_provenance(self):
        root = channel_schedule_catalog_to_xmltv(fixture("channel_schedule-catalog.xml"), now=NOW)
        stats = validate_guide_root(root, now=NOW)
        self.assertEqual(root.get("data-granularity"), "block")
        self.assertEqual(root.find("channel").get("id"), CHANNEL_SCHEDULE_ID)
        self.assertGreater(stats.future_programmes, 0)
        titles = [item.findtext("title") for item in root.findall("programme")]
        self.assertNotIn("Evento encerrado", titles)
        self.assertNotIn("Fora da grade", titles)
        overnight = next(item for item in root.findall("programme") if item.findtext("title") == "Madrugada")
        self.assertEqual(overnight.get("start"), "20261114233000 -0300")
        self.assertEqual(overnight.get("stop"), "20261115003000 -0300")
        replacements = [item for item in root.findall("programme") if item.findtext("title") == "Nova temporada"]
        self.assertTrue(replacements)
        self.assertTrue(all(parse_xmltv_time(item.get("start")) >= datetime(2026, 11, 15, 3, tzinfo=timezone.utc) for item in replacements))
        windows = [(parse_xmltv_time(item.get("start")), parse_xmltv_time(item.get("stop"))) for item in root.findall("programme")]
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(windows, windows[1:])))
        movie = [item for item in root.findall("programme") if item.findtext("title") == "Filme de domingo"]
        self.assertTrue(movie)
        self.assertTrue(all(item.get("start").startswith("20261115") for item in movie))

    def test_channel_schedule_does_not_fill_missing_intervals_or_shift_old_xmltv(self):
        after_schedule = datetime(2026, 11, 14, 21, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "current and future"):
            channel_schedule_catalog_to_xmltv(fixture("channel_schedule-catalog.xml"), now=after_schedule)
        client = Mock()
        client.get_bytes.side_effect = [b'<tv><channel id="ExampleChannel124.br"/><programme channel="ExampleChannel124.br" start="20261031110000 -0300" stop="20261031120000 -0300"><title>Expired</title></programme></tv>', fixture("channel_schedule-catalog.xml")]
        root = fetch_channel_schedule(client, now=NOW)
        self.assertEqual(root.get("data-origin"), "declared-catalog")
        self.assertTrue(has_coverage(root, CHANNEL_SCHEDULE_ID, NOW))
        self.assertEqual([call.args[0] for call in client.get_bytes.call_args_list], [CHANNEL_SCHEDULE_XMLTV, CHANNEL_SCHEDULE_CATALOG])

    def test_weekday_series_preserve_source_titles_without_inferred_episodes(self):
        now = datetime(2026, 11, 16, 16, 10, tzinfo=timezone.utc)
        root = channel_schedule_catalog_to_xmltv(fixture("channel_schedule-series.xml"), now=now)
        current = next(item for item in root.findall("programme")
                       if parse_xmltv_time(item.get("start")) <= now < parse_xmltv_time(item.get("stop")))
        self.assertEqual(current.findtext("title"), "Série de dias úteis")
        self.assertEqual(root.get("data-granularity"), "series-and-block")
        self.assertIsNone(current.find("episode-num"))
        self.assertIsNone(current.find("sub-title"))
        midnight = next(item for item in root.findall("programme")
                        if item.get("start") == "20261117000000 -0300")
        self.assertEqual(midnight.findtext("title"), "Série de dias úteis")

    def test_finite_series_consume_associated_event_and_pause_for_newer_show(self):
        now = datetime(2026, 11, 16, 15, 10, tzinfo=timezone.utc)
        root = channel_schedule_catalog_to_xmltv(fixture("channel_schedule-series.xml"), now=now, days=5)
        by_start = {item.get("start"): item.findtext("title") for item in root.findall("programme")}
        self.assertEqual(by_start["20261116120000 -0300"], "Especial associado à série")
        self.assertEqual(by_start["20261117120000 -0300"], "Substituição mais recente")
        # The associated event consumes the first original. The replacement
        # pauses this cycle on Tuesday; its second original resumes Wednesday.
        self.assertEqual(by_start["20261118120000 -0300"], "Série de dias úteis")
        self.assertNotEqual(by_start.get("20261119120000 -0300"), "Série de dias úteis")
        expired_reruns = [item for item in root.findall("programme")
                         if item.findtext("title") == "Série de dias úteis" and
                         parse_xmltv_time(item.get("start")) >= datetime(2026, 11, 19, 15, tzinfo=timezone.utc)]
        self.assertEqual(expired_reruns, [])
        self.assertEqual(by_start["20261119113000 -0300"], "Ciclo reiniciado")
        restarted = [item for item in root.findall("programme")
                     if item.findtext("title") == "Ciclo reiniciado"]
        self.assertEqual(len(restarted), 1)  # Monday's first cycle has already ended.

    def test_channel_schedule_uses_live_xmltv_and_remaps_every_reference(self):
        root = ET.Element("tv")
        ET.SubElement(root, "channel", {"id": "ExampleChannel124.br"})
        for start, stop in [(NOW - timedelta(hours=1), NOW + timedelta(hours=1)), (NOW + timedelta(hours=1), NOW + timedelta(hours=2))]:
            item = ET.SubElement(root, "programme", {"channel": "ExampleChannel124.br", "start": start.strftime("%Y%m%d%H%M%S %z"), "stop": stop.strftime("%Y%m%d%H%M%S %z")})
            ET.SubElement(item, "title").text = "Published title"
        client = Mock()
        client.get_bytes.return_value = ET.tostring(root)
        actual = fetch_channel_schedule(client, now=NOW)
        self.assertEqual(actual.get("data-origin"), "published-xmltv")
        self.assertTrue(all(item.get("channel") == CHANNEL_SCHEDULE_ID for item in actual.findall("programme")))
        self.assertEqual(client.get_bytes.call_count, 1)

    def test_source_cache_expires_and_outage_does_not_extend_it(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            client = Mock()
            client.get_bytes.return_value = b"source"
            self.assertEqual(cached_payload(client, "https://example.test/epg", cache_dir=cache, ttl_seconds=3600, now=datetime.now(timezone.utc)), b"source")
            cached_payload(client, "https://example.test/epg", cache_dir=cache, ttl_seconds=3600, now=datetime.now(timezone.utc))
            self.assertEqual(client.get_bytes.call_count, 1)
            target = next(cache.iterdir())
            before = NOW.timestamp() - 7200
            os.utime(target, (before, before))
            client.get_bytes.side_effect = RuntimeError("offline")
            with self.assertRaises(RuntimeError):
                cached_payload(client, "https://example.test/epg", cache_dir=cache, ttl_seconds=3600, now=NOW)
            self.assertEqual(target.stat().st_mtime, before)

    def test_schedule_site_a_jsonld_preserves_explicit_offset_and_midnight(self):
        slots = parse_schedule_site_a_html(fixture("schedule_site_a.html"))
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].start.utcoffset(), timedelta(hours=-3))
        self.assertEqual(slots[0].stop.day, 15)
        self.assertEqual(slots[0].title, "Atual & próximos")

    def test_schedule_site_b_year_rollover_and_unknown_last_stop(self):
        now = datetime(2027, 1, 1, 3, tzinfo=timezone.utc)
        slots = parse_schedule_site_b_html(fixture("schedule_site_b.html"), now=now)
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].start.year, 2026)
        self.assertEqual(slots[0].stop.year, 2027)
        self.assertEqual(slots[0].title, "Virada & animação")
        self.assertNotIn("Fim desconhecido", [slot.title for slot in slots])

    def test_html_collection_reports_partial_failure_and_keeps_healthy_channel(self):
        manifest = {"channels": [{"id": "ExampleChannel005.br", "url": "https://www.schedule-site-a.example.invalid/canal/ExampleChannel005"}, {"id": "Other.br", "url": "https://www.schedule-site-a.example.invalid/canal/other"}]}
        client = Mock()
        client.get_bytes.side_effect = [json.dumps(manifest).encode(), fixture("schedule_site_a.html"), RuntimeError("offline")]
        with patch("legal_iptv.services.epg_collectors.time.sleep") as sleeper:
            root = fetch_html_schedules(client, "https://example.test/map.json", "schedule_site_a", now=datetime(2026, 11, 15, 3, tzinfo=timezone.utc))
        self.assertEqual([channel.get("id") for channel in root.findall("channel")], ["ExampleChannel005.br"])
        self.assertEqual(json.loads(root.get("data-failed-channels")), ["Other.br"])
        sleeper.assert_called_once_with(1)

    def test_local_schedule_providers_are_configurable(self):
        providers = load_epg_providers()
        self.assertTrue(any(item.format == "channel_schedule" for item in providers))
        self.assertTrue(all(".example.invalid/" in item.url for item in providers if not item.format.endswith("_mapped")))
        with patch.dict(os.environ, {"IPTV_EPG_CHANNEL_SCHEDULE_ENABLED": "false"}):
            self.assertFalse(any(item.format == "channel_schedule" for item in load_epg_providers()))

    def test_required_channel_schedule_repairs_fresh_guide_using_only_priority_collector(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "guide.xml"
            root = ET.Element("tv")
            ET.SubElement(root, "channel", {"id": "Other.br"})
            for start, stop in [(NOW - timedelta(hours=1), NOW + timedelta(hours=1)), (NOW + timedelta(hours=1), NOW + timedelta(hours=2))]:
                item = ET.SubElement(root, "programme", {"channel": "Other.br", "start": start.strftime("%Y%m%d%H%M%S %z"), "stop": stop.strftime("%Y%m%d%H%M%S %z")})
                ET.SubElement(item, "title").text = "Other title"
            ET.ElementTree(root).write(output)
            fox = EpgProvider("channel_schedule", CHANNEL_SCHEDULE_CATALOG, format="channel_schedule")
            broad = Mock()
            broad.id = "broad"
            clock = Mock(wraps=datetime)
            clock.now.return_value = NOW
            fox_root = channel_schedule_catalog_to_xmltv(fixture("channel_schedule-catalog.xml"), now=NOW)
            with patch("legal_iptv.services.guide.datetime", clock), patch.object(EpgProvider, "fetch", return_value=fox_root) as fetch:
                result = ensure_guide(Mock(), output_path=output, diagnostics_path=output.with_suffix(".json"), ttl_seconds=3600, lkg_seconds=7200, min_coverage_ratio=.5, providers=[fox, broad], required_channel_ids=(CHANNEL_SCHEDULE_ID,))
            self.assertEqual(result.status, "updated")
            fetch.assert_called_once()
            broad.fetch.assert_not_called()
            self.assertTrue(has_coverage(ET.parse(output).getroot(), CHANNEL_SCHEDULE_ID, NOW))
            diagnostic = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(diagnostic["providers"][0]["granularity"], "block")

    def test_required_channel_cannot_be_hidden_by_global_coverage_or_lkg(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "guide.xml"
            healthy = channel_schedule_catalog_to_xmltv(fixture("channel_schedule-catalog.xml"), now=NOW)
            for item in healthy:
                if item.get("id"):
                    item.set("id", "Other.br")
                if item.get("channel"):
                    item.set("channel", "Other.br")
            ET.ElementTree(healthy).write(output)
            provider = Mock()
            provider.id = "only-other-channel"
            provider.fetch.return_value = healthy
            clock = Mock(wraps=datetime)
            clock.now.return_value = NOW
            with patch("legal_iptv.services.guide.datetime", clock), self.assertRaisesRegex(ValueError, "Required EPG"):
                ensure_guide(Mock(), output_path=output, diagnostics_path=output.with_suffix(".json"), ttl_seconds=3600, lkg_seconds=7200, min_coverage_ratio=.5, providers=[provider], required_channel_ids=(CHANNEL_SCHEDULE_ID,))


if __name__ == "__main__":
    unittest.main()
