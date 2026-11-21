import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from legal_iptv.models import Channel
from legal_iptv.models.stream_validation import UrlValidation
from legal_iptv.services.link_validator import (
    filter_cached_offline_channels,
    filter_media_confirmed_channels,
    refresh_stream_status,
)
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.services.validation_schedule import context_for_channel

NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def channel(**kwargs):
    values = dict(id='example', name='Example', stream_url='https://example.test/live.m3u8',
                  logo='', group='News', source='extra')
    return Channel(**{**values, **kwargs})


def success_record(current, **changes):
    return {
        'context_id': context_for_channel(current).id,
        'probe_version': 2,
        'validation_level': 'transport_sample',
        'active': True,
        'status': 'active',
        'checked_at': NOW.isoformat(),
        'consecutive_failures': 0,
        **changes,
    }


class MediaProofTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'status.json'
        self.clock = patch('legal_iptv.services.link_validator._utc_now', return_value=NOW).start()
        self.addCleanup(patch.stopall)

    def write(self, records):
        self.path.write_text(json.dumps({'urls': records}))

    def selected(self, channels=None, **kwargs):
        return filter_media_confirmed_channels(
            channels or [channel()], status_file=self.path, **kwargs,
        )

    def refresh(self, result, channels=None, force=True):
        with patch('legal_iptv.services.link_validator.inspect_url', return_value=result):
            return refresh_stream_status(
                channels or [channel()], status_file=self.path, max_workers=1,
                timeout=1, max_age_seconds=14400, force=force,
            )

    def record(self):
        return json.loads(self.path.read_text())['urls'][channel().stream_url]

    def test_missing_initial_unknown_and_head_legacy_are_not_media_proof(self):
        self.assertEqual(self.selected(), [])
        cases = [
            {'active': True, 'checked_at': NOW.isoformat()},
            success_record(channel(), probe_version=1),
            success_record(channel(), validation_level='http'),
            success_record(channel(), active=None, status='unknown'),
            success_record(channel(), active=False, status='offline'),
            success_record(channel(), status='offline'),
            success_record(channel(), checked_at='invalid'),
            success_record(channel(), checked_at=(NOW + timedelta(seconds=1)).isoformat()),
        ]
        for record in cases:
            with self.subTest(record=record):
                self.write({channel().stream_url: record})
                self.assertEqual(self.selected(), [])

    def test_preexisting_version_two_sampled_success_needs_no_extra_probe(self):
        self.write({channel().stream_url: success_record(channel())})
        with patch('legal_iptv.services.link_validator.inspect_url') as probe:
            self.assertEqual(self.selected(), [channel()])
        probe.assert_not_called()

    def test_initial_inconclusive_is_rechecked_but_not_published_as_proven(self):
        self.refresh(UrlValidation(None, 503))
        self.assertIsNone(self.record()['active'])
        self.assertNotIn('last_media_success_at', self.record())
        self.assertEqual(self.selected(), [])
        self.assertEqual(filter_cached_offline_channels([channel()], status_file=self.path, max_age_seconds=86400), [channel()])
        self.refresh(UrlValidation(True, 200))
        self.assertEqual(self.selected(), [channel()])

    def test_one_inconclusive_retry_preserves_exact_success_time(self):
        self.refresh(UrlValidation(True, 200))
        self.assertEqual(self.record()['last_media_success_at'], NOW.isoformat())
        self.clock.return_value = NOW + timedelta(hours=2)
        self.refresh(UrlValidation(None, 503))
        record = self.record()
        self.assertIsNone(record['active'])
        self.assertEqual(record['consecutive_failures'], 1)
        self.assertEqual(record['last_media_success_at'], NOW.isoformat())
        self.assertEqual(record['checked_at'], self.clock.return_value.isoformat())
        self.assertEqual(self.selected(), [channel()])
        self.clock.return_value = NOW + timedelta(hours=24, seconds=1)
        self.assertEqual(self.selected(), [])

    def test_legacy_positive_cannot_be_promoted_to_sampled_lkg(self):
        self.write({channel().stream_url: {
            'context_id': context_for_channel(channel()).id,
            'active': True, 'status': 'active', 'checked_at': NOW.isoformat(),
        }})
        self.refresh(UrlValidation(None, 503))
        self.assertNotIn('last_media_success_at', self.record())
        self.assertEqual(self.selected(), [])

    def test_second_failure_or_definitive_rejection_cannot_use_lkg(self):
        for failure in (UrlValidation(None, 503), UrlValidation(False, 404)):
            with self.subTest(failure=failure):
                self.path.unlink(missing_ok=True)
                self.refresh(UrlValidation(True, 200))
                self.clock.return_value += timedelta(minutes=1)
                self.refresh(failure)
                if failure.active is None:
                    self.assertEqual(self.selected(), [channel()])
                    self.refresh(failure)
                self.assertEqual(self.selected(), [])
                self.refresh(UrlValidation(None, 503))
                self.assertEqual(self.selected(), [])
                self.refresh(UrlValidation(True, 200))
                self.assertEqual(self.selected(), [channel()])

    def test_fresh_unknown_check_does_not_renew_aged_success(self):
        old = (NOW - timedelta(days=2)).isoformat()
        self.write({channel().stream_url: success_record(
            channel(), active=None, status='unknown', last_media_success_at=old,
            consecutive_failures=1,
        )})
        self.assertEqual(self.selected(), [])

    def test_headers_context_and_expiry_are_never_shared(self):
        original = channel(request_headers={'Referer': 'https://example.test/one'})
        other = replace(original, request_headers={'Referer': 'https://example.test/two'})
        self.write({original.stream_url: success_record(original)})
        self.assertEqual(self.selected([original, other]), [original])
        expired = channel(expires_at=(NOW - timedelta(seconds=1)).isoformat())
        self.write({expired.stream_url: success_record(expired)})
        self.assertEqual(self.selected([expired]), [])

    def test_unproven_priority_source_does_not_mask_confirmed_lower_source(self):
        high = channel(source='live_stream_catalog', provider_id='directory_catalog')
        lower = channel(id='lower', stream_url='https://official.test/live.m3u8', source='public_catalog')
        self.write({
            high.stream_url: success_record(high, active=None, status='unknown'),
            lower.stream_url: success_record(lower),
        })
        proven = self.selected([high, lower])
        selected = select_profile_channels(proven, load_playlist_configuration(), 'full')
        self.assertEqual([item.stream_url for item in selected], [lower.stream_url])

    def test_reused_cache_never_refreshes_last_success_timestamp(self):
        self.refresh(UrlValidation(True, 200))
        self.clock.return_value += timedelta(hours=1)
        self.refresh(UrlValidation(True, 200), force=False)
        self.assertEqual(self.record()['validation'], 'cached')
        self.assertEqual(self.record()['last_media_success_at'], NOW.isoformat())


if __name__ == '__main__':
    unittest.main()
