import gzip
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.services.epg_cache import cached_source, prune_cache
from legal_iptv.services.epg_collectors import fetch_official_schedule, fetch_mapped_schedules, parse_official_schedule_a_html, parse_official_schedule_b_html
from legal_iptv.services.epg_providers import EpgProvider
from legal_iptv.services.guide import ensure_guide, guide_coverage, load_guide_inventory_aliases, merge_provider_guides, parse_xmltv_time, project_guide, read_xmltv_source

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def guide(ids, now=NOW):
    root = ET.Element('tv')
    for identifier in ids:
        channel = ET.SubElement(root, 'channel', {'id': identifier})
        ET.SubElement(channel, 'display-name').text = identifier.removesuffix('.br')
        for index, (a, b) in enumerate([(-1, 1), (1, 2)]):
            item = ET.SubElement(root, 'programme', {'channel': identifier, 'start': (now + timedelta(hours=a)).strftime('%Y%m%d%H%M%S %z'), 'stop': (now + timedelta(hours=b)).strftime('%Y%m%d%H%M%S %z')})
            ET.SubElement(item, 'title').text = f'Published {index}'
            ET.SubElement(item, 'desc').text = 'Descriptions are not needed in the compact guide.'
    return root


def station_a_row(start='10:15', stop='12:05'):
    return f'<div class="group row"><span>{start}</span><span>{stop}</span><h3>Published session</h3><button onclick="abrirDetalhes({{&quot;tipo&quot;:&quot;program&quot;,&quot;grade_ep&quot;:null}})">Details</button></div>'.encode()


class GuideProjectionTest(unittest.TestCase):
    def test_projection_declares_unscheduled_playlist_ids_without_programmes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.xml'
            ET.ElementTree(guide(['ExampleChannel053.br'])).write(path)
            result = project_guide(path, path, {'ExampleChannel053.br', 'Missing.br'}, now=NOW,
                                   required_channel_ids=('ExampleChannel053.br',),
                                   channel_names={'Missing.br': 'Friendly Missing'})
            actual = ET.parse(path).getroot()
            self.assertEqual((result.stats.channels, result.stats.programmes), (2, 2))
            self.assertEqual(actual.find("channel[@id='Missing.br']/display-name").text, 'Friendly Missing')
            self.assertIsNone(actual.find("programme[@channel='Missing.br']"))
            self.assertEqual(guide_coverage(actual, {'Missing.br'}, now=NOW)['Missing.br']['status'], 'missing')
            self.assertEqual([item.tag for item in actual], ['channel', 'channel', 'programme', 'programme'])

    def test_unscheduled_declarations_do_not_cause_false_lkg_coverage_drop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.xml'
            now = datetime.now(timezone.utc)
            ET.ElementTree(guide(['ExampleChannel053.br'], now)).write(path)
            selected = {'ExampleChannel053.br', 'Missing.br', 'Another.br'}
            project_guide(path, path, selected, now=now, required_channel_ids=('ExampleChannel053.br',))
            provider = Mock()
            provider.id = 'mock'
            provider.fetch.return_value = guide(['ExampleChannel053.br'], now)
            os.utime(path, (now.timestamp() - 120, now.timestamp() - 120))
            result = ensure_guide(Mock(), output_path=path, diagnostics_path=path.with_suffix('.json'),
                                  ttl_seconds=60, lkg_seconds=3600, min_coverage_ratio=.8,
                                  providers=[provider], selected_channel_ids=selected,
                                  required_channel_ids=('ExampleChannel053.br',))
            self.assertEqual(result.status, 'updated')

    def test_stream_projection_same_path_without_tree_parse_preserves_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.xml'
            ET.ElementTree(guide(['ExampleChannel053.br', 'Other.br'])).write(path)
            old = NOW.timestamp() - 600
            os.utime(path, (old, old))
            with patch('legal_iptv.services.guide.ET.parse', side_effect=AssertionError('Full-tree parse')):
                result = project_guide(path, path, {'ExampleChannel053.br'}, now=NOW, required_channel_ids=('ExampleChannel053.br',))
            self.assertEqual((result.stats.channels, result.stats.programmes), (1, 2))
            self.assertEqual(path.stat().st_mtime, old)
            self.assertIsNone(ET.parse(path).getroot().find('programme/desc'))
            self.assertEqual(list(path.parent.glob('epg-*.tmp')), [])

    def test_gzip_stream_discards_many_unselected_records_and_keeps_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.gz'
            source = guide(['ExampleChannel053.br', 'Other.br'])
            with gzip.open(path, 'wb') as output:
                output.write(b'<tv>')
                for item in source.findall('channel'): output.write(ET.tostring(item))
                item = source.findall('programme')[2]
                item.find('desc').text = 'x' * 8192
                for _ in range(1000): output.write(ET.tostring(item))
                for item in source.findall('programme')[:2]: output.write(ET.tostring(item))
                output.write(b'</tv>')
            inventory = {}
            actual = read_xmltv_source(path, channel_ids={'ExampleChannel053.br'}, now=NOW, inventory=inventory)
            self.assertEqual(len(actual.findall('programme')), 2)
            self.assertEqual(inventory['Other.br']['programme_count'], 1000)
            self.assertLess(len(ET.tostring(actual)), 2000)

    def test_priority_fills_only_uncovered_intervals(self):
        high, low = guide(['ExampleChannel053.br']), guide(['ExampleChannel053.br'])
        high.remove(high.findall('programme')[0])
        high.find('programme/title').text = 'Official future'
        low.findall('programme')[0].set('stop', (NOW + timedelta(hours=1, minutes=30)).strftime('%Y%m%d%H%M%S %z'))
        low.findall('programme')[1].set('stop', (NOW + timedelta(hours=3)).strftime('%Y%m%d%H%M%S %z'))
        actual = merge_provider_guides([high, low], now=NOW)
        windows = [(parse_xmltv_time(x.get('start')), parse_xmltv_time(x.get('stop'))) for x in actual.findall('programme')]
        self.assertEqual(len(windows), 3)
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(windows, windows[1:])))
        self.assertEqual(actual.findall('programme')[1].findtext('title'), 'Official future')
        self.assertEqual(guide_coverage(actual, {'ExampleChannel053.br'}, now=NOW)['ExampleChannel053.br']['status'], 'current_and_future')

    def test_no_data_does_not_mask_lower_source(self):
        high = guide(['ExampleChannel053.br'])
        for item in high.findall('programme'): item.find('title').text = 'No Data'
        actual = merge_provider_guides([high, guide(['ExampleChannel053.br'])], now=NOW)
        self.assertEqual([x.findtext('title') for x in actual.findall('programme')], ['Published 0', 'Published 1'])

    def test_failed_atomic_write_preserves_input_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.xml'
            ET.ElementTree(guide(['ExampleChannel053.br'])).write(path)
            before = path.read_bytes()
            with patch('legal_iptv.services.guide.ET.ElementTree.write', side_effect=OSError('full')), self.assertRaises(OSError):
                project_guide(path, path, {'ExampleChannel053.br'}, now=NOW)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('epg-*.tmp')), [])

    def test_selection_change_reparses_upstream_cache_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.xml'
            client = Mock()
            client.get_bytes.return_value = ET.tostring(guide(['ExampleChannel053.br', 'Other.br'], datetime.now(timezone.utc)))
            args = dict(output_path=path, diagnostics_path=path.with_suffix('.json'), ttl_seconds=3600, lkg_seconds=7200, min_coverage_ratio=.5, providers=[EpgProvider('xml', 'https://example.test/guide.xml')], required_channel_ids=('ExampleChannel053.br',))
            ensure_guide(client, selected_channel_ids={'ExampleChannel053.br'}, **args)
            self.assertEqual(load_guide_inventory_aliases(path.parent / '.epg-cache')['other'].tvg_id, 'Other.br')
            ensure_guide(client, selected_channel_ids={'ExampleChannel053.br', 'Other.br'}, **args)
            self.assertEqual(client.get_bytes.call_count, 1)
            self.assertEqual(len(ET.parse(path).getroot().findall('channel')), 2)

    def test_legacy_fresh_guide_migrates_without_http_or_mtime_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.xml'
            ET.ElementTree(guide(['ExampleChannel053.br'], datetime.now(timezone.utc))).write(path)
            before = path.stat().st_mtime
            provider = Mock()
            result = ensure_guide(Mock(), output_path=path, diagnostics_path=path.with_suffix('.json'), ttl_seconds=3600, lkg_seconds=7200, min_coverage_ratio=.5, providers=[provider], selected_channel_ids={'ExampleChannel053.br'}, required_channel_ids=('ExampleChannel053.br',))
            self.assertEqual(result.status, 'fresh')
            provider.fetch.assert_not_called()
            self.assertEqual(path.stat().st_mtime, before)

    def test_inventory_ambiguous_names_are_not_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'channel-inventory.json'
            path.write_text(json.dumps({'version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(), 'channels': [{'id': 'One.br', 'names': ['Ambiguous', 'Exact One'], 'programme_count': 2}, {'id': 'Two.br', 'names': ['Ambiguous'], 'programme_count': 2}, {'id': 'Empty.br', 'names': ['Empty'], 'programme_count': 0}]}))
            aliases = load_guide_inventory_aliases(path.parent)
            self.assertNotIn('ambiguous', aliases)
            self.assertNotIn('empty', aliases)
            self.assertEqual(aliases['exact one'].tvg_id, 'One.br')

    def test_cache_prunes_age_count_and_bytes_but_keeps_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(4):
                path = root / (f'{index:064x}.data')
                path.write_bytes(b'12345678')
                stamp = NOW.timestamp() - index * 10
                os.utime(path, (stamp, stamp))
            (root / 'notes.txt').write_text('keep')
            prune_cache(root, now=NOW, max_bytes=16, max_files=2, max_age_seconds=25)
            self.assertEqual(len(list(root.glob('*.data'))), 2)
            self.assertEqual((root / 'notes.txt').read_text(), 'keep')

    def test_lkg_has_original_date_expires_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.get_bytes.return_value = b'known source'
            with cached_source(client, 'https://example.test/source', cache_dir=root, ttl_seconds=60, lkg_seconds=3600, now=NOW) as source: self.assertEqual(source.path.read_bytes(), b'known source')
            path = next(root.glob('*.data'))
            stamp = path.stat().st_mtime
            client.get_bytes.side_effect = RuntimeError('offline')
            with cached_source(client, 'https://example.test/source', cache_dir=root, ttl_seconds=60, lkg_seconds=3600, now=NOW + timedelta(minutes=2)) as source:
                self.assertEqual((source.origin, source.fetched_at), ('lkg', NOW))
            self.assertEqual(path.stat().st_mtime, stamp)
            with self.assertRaises(RuntimeError), cached_source(client, 'https://example.test/source', cache_dir=root, ttl_seconds=60, lkg_seconds=3600, now=NOW + timedelta(hours=2)): pass
            self.assertEqual(list(root.glob('epg-*.tmp')), [])

    def test_station_a_explicit_overnight_hours_and_no_inferred_episode(self):
        slots = parse_official_schedule_a_html(station_a_row('23:30', '00:15'), NOW.date())
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].stop - slots[0].start, timedelta(minutes=45))
        self.assertEqual(slots[0].stop.date(), NOW.date() + timedelta(days=1))
        self.assertEqual(slots[0].start.utcoffset(), timedelta(hours=-3))

    def test_station_a_only_selected_three_dates_and_partial_failures(self):
        client = Mock()
        client.get_bytes.side_effect = [station_a_row(), b'<p>Nenhuma programa\xc3\xa7\xc3\xa3o agendada</p>', RuntimeError('offline')]
        with patch('legal_iptv.services.epg_collectors.time.sleep'):
            root = fetch_official_schedule(client, 'https://www.official_schedule_a.com.br/render_grade.php', 'official_schedule_a', now=NOW, channel_ids={'ExampleChannel054.br'})
        self.assertEqual(client.get_bytes.call_count, 3)
        self.assertEqual(len(root.findall('programme')), 1)
        self.assertEqual(json.loads(root.get('data-failed-dates')), ['2026-11-17'])
        self.assertIsNone(root.find('programme/episode-num'))
        client.reset_mock()
        fetch_official_schedule(client, 'https://www.official_schedule_a.com.br/render_grade.php', 'official_schedule_a', now=NOW, channel_ids={'Other.br'})
        client.get_bytes.assert_not_called()

    def test_official_schedule_b_empty_week_stays_empty(self):
        self.assertEqual(parse_official_schedule_b_html('Nenhuma programação cadastrada para Segunda'.encode('latin1'), now=NOW), [])

    def test_mapped_schedule_site_b_uses_only_exact_selected_identity(self):
        client = Mock()
        client.get_bytes.return_value = (Path(__file__).parents[1] / 'fixtures/epg/schedule_site_b.html').read_bytes()
        root = fetch_mapped_schedules(client, 'schedule_site_b', now=datetime(2027, 1, 1, 3, tzinfo=timezone.utc), channel_ids={'Selected.br', 'Unmapped.br'}, channel_names={'Selected.br': 'Example Channel 261', 'Unmapped.br': 'Example Channel 261 Acre'})
        self.assertEqual(client.get_bytes.call_count, 1)
        self.assertIn('/example-channel-61', client.get_bytes.call_args.args[0])
        self.assertEqual([x.get('id') for x in root.findall('channel')], ['Selected.br'])



class CacheResourceBudgetTest(unittest.TestCase):
    def test_refresh_preserves_hardlinked_cache_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.get_bytes.return_value = b'old source'
            with cached_source(client, 'https://example.test/source', cache_dir=root, ttl_seconds=60, now=NOW) as source:
                self.assertEqual(source.path.read_bytes(), b'old source')
            path = next(root.glob('*.data'))
            backup = root / 'backup.snapshot'
            os.link(path, backup)
            stamp = backup.stat().st_mtime
            client.get_bytes.return_value = b'new source'
            with cached_source(client, 'https://example.test/source', cache_dir=root, ttl_seconds=60, now=NOW + timedelta(minutes=2)) as source:
                self.assertEqual(source.path.read_bytes(), b'new source')
            self.assertEqual(backup.read_bytes(), b'old source')
            self.assertEqual(backup.stat().st_mtime, stamp)
            self.assertNotEqual(backup.stat().st_ino, path.stat().st_ino)

    def test_abandoned_json_temporary_files_are_pruned_after_one_hour(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, recent = root / '.write-old.tmp', root / '.write-recent.tmp'
            old.write_bytes(b'old')
            recent.write_bytes(b'active')
            os.utime(old, (NOW.timestamp() - 4000, NOW.timestamp() - 4000))
            os.utime(recent, (NOW.timestamp(), NOW.timestamp()))
            prune_cache(root, now=NOW)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

    def test_low_disk_stops_before_downloading_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            with patch('legal_iptv.services.epg_cache._headroom', side_effect=OSError('low space')):
                with self.assertRaises(OSError), cached_source(client, 'https://example.test/source', cache_dir=Path(directory), ttl_seconds=60, now=NOW): pass
            client.get_bytes.assert_not_called()
            self.assertEqual(list(Path(directory).glob('epg-*.tmp')), [])

    def test_compressed_xml_cannot_bypass_decoded_byte_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.gz'
            with gzip.open(path, 'wb') as output: output.write(b'<tv>' + b' ' * 8192 + b'</tv>')
            with patch.dict(os.environ, {'IPTV_EPG_UPSTREAM_MAX_BYTES': '1024'}), self.assertRaisesRegex(ValueError, 'byte budget'):
                read_xmltv_source(path, channel_ids={'ExampleChannel053.br'}, now=NOW)

    def test_production_downloader_streams_bounded_chunks_without_response_content(self):
        import requests
        from legal_iptv.clients import HttpClient
        class Response:
            status_code = 200
            headers = {}
            @property
            def content(self): raise AssertionError('Body must not be materialized')
            def raise_for_status(self): pass
            def iter_content(self, size): yield b'1234'; yield b'5678'; yield b'overflow'
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            client = HttpClient(retries=1)
            client.session.close()
            client.session = Mock(spec=requests.Session)
            client.session.get.return_value = Response()
            with self.assertRaisesRegex(ValueError, 'byte budget'), cached_source(client, 'https://example.test/source', cache_dir=Path(directory), ttl_seconds=60, now=NOW, max_bytes=8): pass
            self.assertTrue(client.session.get.call_args.kwargs['stream'])
            self.assertEqual(list(Path(directory).glob('epg-*.tmp')), [])


    def test_malformed_download_does_not_replace_valid_lkg_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Mock()
            client.get_bytes.return_value = b'<tv/>'
            with cached_source(client, 'https://example.test/source', cache_dir=root, ttl_seconds=60, now=NOW) as source:
                ET.parse(source.path)
            path = next(root.glob('*.data'))
            original_stamp = path.stat().st_mtime
            client.get_bytes.return_value = b'<html broken'
            with self.assertRaises(ET.ParseError), cached_source(client, 'https://example.test/source', cache_dir=root, ttl_seconds=60, now=NOW + timedelta(minutes=2)) as source:
                ET.parse(source.path)
            self.assertEqual(path.read_bytes(), b'<tv/>')
            self.assertEqual(path.stat().st_mtime, original_stamp)
            self.assertEqual(list(root.glob('epg-*.tmp')), [])


if __name__ == '__main__': unittest.main()
