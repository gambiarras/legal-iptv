import unittest
from unittest.mock import Mock, patch

import requests
from urllib3.exceptions import ProtocolError

from legal_iptv.services.transport_probe import MEDIA_BYTES, probe_transport

TS = (b'G' + b'\0' * 187) * 400
PLAYLIST = b'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nold.ts\n#EXTINF:6,\nnow.ts\n'
ROOT = 'https://stream.test/live/index.m3u8'


class Response:
    def __init__(self, body=b'', status=200, headers=None, raw=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = body
        self.raw = raw
        self.closed = False
        self.yielded_bytes = 0

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            chunk = self.body[index:index + chunk_size]
            self.yielded_bytes += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


class TransportProbeTest(unittest.TestCase):
    def run_probe(self, responses, url=ROOT, headers=None):
        session = Mock()
        session.get.side_effect = responses
        return probe_transport(session, url, 3, headers or {}), session

    def test_hls_samples_last_segment_immediately_and_closes_every_response(self):
        responses = [Response(PLAYLIST), Response(TS, 206)]
        result, session = self.run_probe(responses)
        self.assertTrue(result.active)
        self.assertEqual(result.request_count, 2)
        self.assertEqual([call.args[0] for call in session.get.call_args_list], [ROOT, 'https://stream.test/live/now.ts'])
        self.assertEqual(session.get.call_args.kwargs['headers']['range'], f'bytes=0-{MEDIA_BYTES - 1}')
        self.assertTrue(all(response.closed for response in responses))

    def test_master_selects_one_low_bandwidth_rendition(self):
        master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=999999\nhigh/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=10000\nlow/index.m3u8\n'
        result, session = self.run_probe([Response(master), Response(PLAYLIST), Response(TS)])
        self.assertTrue(result.active)
        self.assertEqual(result.request_count, 3)
        self.assertEqual(session.get.call_args_list[1].args[0], 'https://stream.test/live/low/index.m3u8')
        self.assertEqual(session.get.call_args_list[2].args[0], 'https://stream.test/live/low/now.ts')

    def test_html_with_http_200_is_rejected_at_manifest_or_segment(self):
        for responses in ([Response(b'<html>player</html>')], [Response(PLAYLIST), Response(b'<!DOCTYPE html>error')]):
            with self.subTest(responses=len(responses)):
                result, _ = self.run_probe(responses)
                self.assertFalse(result.active)
                self.assertEqual(result.error_type, 'InvalidMediaContent')
                self.assertEqual(result.http_status, 200)

    def test_segment_missing_is_distinct_from_root_missing(self):
        root, _ = self.run_probe([Response(status=404)])
        media, _ = self.run_probe([Response(PLAYLIST), Response(status=404), Response(status=404)])
        self.assertFalse(root.active)
        self.assertIsNone(media.active)
        self.assertEqual(root.error_type, 'HttpNotFound')
        self.assertEqual(media.error_type, 'MediaNotFound')

    def test_latest_segment_race_falls_back_to_previous_segment(self):
        result, session = self.run_probe([Response(PLAYLIST), Response(status=404), Response(TS)])
        self.assertTrue(result.active)
        self.assertEqual(result.request_count, 3)
        self.assertEqual(session.get.call_args.args[0], 'https://stream.test/live/old.ts')

    def test_fmp4_requires_initialization_and_fragment_payload(self):
        playlist = b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nmedia.m4s\n'
        for initialization, active in ((b'\0\0\0\x18ftypiso6', True), (b'<html>error</html>', False)):
            with self.subTest(active=active):
                result, _ = self.run_probe([Response(playlist), Response(initialization), Response(b'\0\0\0\x08moof\0\0\0\x10mdat12345678')])
                self.assertIs(result.active, active)

    def test_direct_ts_is_sampled_without_head_and_honors_byte_cap(self):
        response = Response(TS * 100)
        result, session = self.run_probe([response], url='https://stream.test/live.ts')
        self.assertTrue(result.active)
        self.assertEqual(response.yielded_bytes, MEDIA_BYTES)
        session.head.assert_not_called()

    def test_media_server_ignoring_range_does_not_cause_unbounded_download(self):
        raw = Mock()
        remaining = TS * 100
        consumed = 0
        def read1(amount, decode_content):
            nonlocal consumed
            value = remaining[consumed:consumed + amount]
            consumed += len(value)
            return value
        raw.read1.side_effect = read1
        result, _ = self.run_probe([Response(PLAYLIST), Response(raw=raw)])
        self.assertTrue(result.active)
        self.assertEqual(consumed, MEDIA_BYTES)
        self.assertLessEqual(max(call.args[0] for call in raw.read1.call_args_list), 4096)

    def test_unbounded_manifest_is_inconclusive_and_closed(self):
        response = Response(b'#EXTM3U\n' + b'#padding\n' * 40000)
        result, _ = self.run_probe([response])
        self.assertIsNone(result.active)
        self.assertEqual(result.error_type, 'ProbeByteLimit')
        self.assertLessEqual(response.yielded_bytes, 262144)
        self.assertTrue(response.closed)

    def test_unimplemented_transports_and_encryption_are_inconclusive(self):
        cases = [
            (b'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"></MPD>', 'DashSamplingUnsupported'),
            (b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:6,\nnow.ts', 'EncryptedHlsUnsupported'),
            (b'#EXTM3U\n#EXT-X-BYTERANGE:100@0\n#EXTINF:6,\nnow.ts', 'HlsByteRangeUnsupported'),
            (b'not a video', 'UnsupportedMediaSample'),
        ]
        for body, error in cases:
            with self.subTest(error=error):
                result, session = self.run_probe([Response(body)])
                self.assertIsNone(result.active)
                self.assertEqual(result.error_type, error)
                self.assertEqual(session.get.call_count, 1)

    def test_redirect_and_foreign_hls_media_strip_credentials(self):
        headers = {'Authorization': 'private', 'Cookie': 'private', 'X-Key': 'private', 'Referer': 'https://watch.test/', 'User-Agent': 'public-agent'}
        redirected = Response(status=302, headers={'Location': 'https://other.test/list.m3u8'})
        result, session = self.run_probe([redirected, Response(PLAYLIST), Response(TS)], headers=headers)
        self.assertTrue(result.active)
        self.assertEqual(session.get.call_args_list[0].kwargs['headers']['authorization'], 'private')
        for call in session.get.call_args_list[1:]:
            self.assertEqual(call.kwargs['headers'], {'user-agent': 'public-agent', **({'range': f'bytes=0-{MEDIA_BYTES - 1}'} if call.args[0].endswith('.ts') else {})})
            self.assertFalse(call.kwargs['allow_redirects'])
        self.assertTrue(redirected.closed)

    def test_same_origin_manifest_variant_and_segment_preserve_declared_headers(self):
        headers = {'Referer': 'https://stream.test/player', 'User-Agent': 'custom'}
        result, session = self.run_probe([Response(PLAYLIST), Response(TS)], headers=headers)
        self.assertTrue(result.active)
        for call in session.get.call_args_list:
            self.assertEqual(call.kwargs['headers']['referer'], headers['Referer'])
            self.assertEqual(call.kwargs['headers']['user-agent'], 'custom')

    def test_redirects_and_nested_masters_are_bounded(self):
        response = Response(status=302, headers={'Location': '/loop'})
        result, session = self.run_probe([response] * 4)
        self.assertIsNone(result.active)
        self.assertEqual(result.error_type, 'RedirectLimit')
        self.assertEqual(session.get.call_count, 4)
        master = Response(b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=10\nchild.m3u8')
        result, session = self.run_probe([master] * 3)
        self.assertIsNone(result.active)
        self.assertEqual(result.error_type, 'HlsVariantLimit')
        self.assertEqual(session.get.call_count, 3)

    def test_io_errors_are_inconclusive_without_private_exception_text(self):
        raw = Mock()
        raw.read1.side_effect = ProtocolError('https://private.test/?token=secret')
        result, _ = self.run_probe([Response(raw=raw)])
        self.assertIsNone(result.active)
        self.assertEqual(result.error_type, 'ProtocolError')
        self.assertNotIn('secret', repr(result))
        result, _ = self.run_probe([requests.Timeout('private query')])
        self.assertIsNone(result.active)
        self.assertEqual(result.error_type, 'Timeout')

    def test_deadline_is_shared_across_manifest_and_segment(self):
        with patch('legal_iptv.services.transport_probe.time.monotonic', side_effect=[0, 0, 0, 0, 4, 4]):
            result, session = self.run_probe([Response(PLAYLIST)])
        self.assertIsNone(result.active)
        self.assertEqual(result.error_type, 'ProbeTimeout')
        self.assertEqual(session.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
