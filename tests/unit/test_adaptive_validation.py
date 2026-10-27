import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel
from legal_iptv.services.link_validator import (
    StreamProbeSession,
    filter_cached_offline_channels,
    refresh_stream_status,
)
from legal_iptv.services.validation_schedule import ValidationPolicy

START = datetime(2026, 10, 27, 0, tzinfo=timezone.utc)


def channel(name="test", url="https://example.test/live.m3u8", **kwargs):
    return Channel(name, name, url, "", "News", "extra", **kwargs)


class AdaptiveValidationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "status.json"
        self.clock = patch(
            "legal_iptv.services.link_validator._utc_now", return_value=START
        ).start()
        self.addCleanup(patch.stopall)
        self.session = Mock()
        self.session.head.return_value.status_code = 200
        self.session.get.return_value.status_code = 404
        patch(
            "legal_iptv.services.link_validator._get_session", return_value=self.session
        ).start()

    def run_validation(self, channels=None, **kwargs):
        return refresh_stream_status(
            [channel()] if channels is None else channels,
            status_file=self.path,
            max_workers=1,
            timeout=1,
            max_age_seconds=14400,
            **kwargs,
        )

    def snapshot(self):
        return json.loads(self.path.read_text())

    def test_replay_reduces_real_http_calls_and_preserves_playlist(self):
        output = {}
        for name, maximum in (("fixed", 14400), ("adaptive", 86400)):
            self.path = Path(self.directory.name) / (name + ".json")
            policy = ValidationPolicy(max_success_seconds=maximum)
            for hour in (0, 4, 12, 24):
                self.clock.return_value = START + timedelta(hours=hour)
                self.run_validation(policy=policy, force=True)
            before = self.session.head.call_count
            for hour in range(28, 49, 4):
                self.clock.return_value = START + timedelta(hours=hour)
                selected = self.run_validation(policy=policy)
                output[name] = render_m3u(selected)
            calls = self.session.head.call_count - before
            self.assertEqual(calls, 6 if name == "fixed" else 1)
            self.assertEqual(
                self.snapshot()["summary"]["provider_metrics"][0]["checks"], 4 + calls
            )
        self.assertEqual(output["fixed"], output["adaptive"])
        self.assertEqual(self.session.get.call_count, 0)

    def test_cache_does_not_extend_observation_or_streak(self):
        self.run_validation()
        first = self.snapshot()
        self.clock.return_value += timedelta(hours=1)
        self.run_validation()
        cached = self.snapshot()
        self.assertEqual(first["history"], cached["history"])
        for key in ("checked_at", "next_check_at", "consecutive_successes"):
            self.assertEqual(
                first["urls"][channel().stream_url][key],
                cached["urls"][channel().stream_url][key],
            )
        self.assertEqual(cached["summary"]["validation_metrics"]["checks"], 0)
        self.assertEqual(self.session.head.call_count, 1)

    def test_failure_backoff_recovers_and_resets_stability(self):
        self.session.head.return_value.status_code = 503
        self.run_validation()
        self.clock.return_value += timedelta(minutes=10)
        self.assertEqual(len(self.run_validation()), 1)
        self.assertEqual(self.session.head.call_count, 1)
        self.clock.return_value += timedelta(minutes=5)
        self.assertEqual(self.run_validation(), [])
        second = self.snapshot()["urls"][channel().stream_url]
        self.assertEqual(second["status"], "temporarily_unavailable")
        self.assertEqual(second["consecutive_successes"], 0)
        self.clock.return_value += timedelta(minutes=20)
        self.assertEqual(self.run_validation(), [])
        self.assertEqual(
            self.snapshot()["urls"][channel().stream_url]["status"],
            "temporarily_unavailable",
        )
        self.assertEqual(self.session.head.call_count, 2)
        self.session.head.return_value.status_code = 200
        self.clock.return_value += timedelta(minutes=10)
        self.assertEqual(len(self.run_validation()), 1)
        recovered = self.snapshot()["urls"][channel().stream_url]
        self.assertEqual(recovered["consecutive_failures"], 0)
        self.assertEqual(recovered["consecutive_successes"], 1)
        self.assertEqual(self.session.head.call_count, 3)

    def test_negative_suppression_expires_without_probes_during_generation(self):
        self.session.head.return_value.status_code = 404
        self.run_validation()
        self.assertEqual(
            filter_cached_offline_channels(
                [channel()], status_file=self.path, max_age_seconds=14400
            ),
            [],
        )
        self.clock.return_value += timedelta(hours=5)
        self.assertEqual(
            filter_cached_offline_channels(
                [channel()], status_file=self.path, max_age_seconds=14400
            ),
            [channel()],
        )
        self.assertEqual(self.session.head.call_count, 1)

    def test_url_headers_or_quality_change_require_a_new_probe(self):
        original = channel(request_headers={"Authorization": "private-a"})
        self.run_validation([original])
        self.run_validation(
            [replace(original, request_headers={"authorization": "private-a"})]
        )
        self.assertEqual(self.session.head.call_count, 1)
        for current in (
            replace(original, request_headers={"Authorization": "private-b"}),
            replace(original, stream_url=original.stream_url + "?new=1"),
            replace(original, resolution="1080p"),
        ):
            self.run_validation([current])
            record = self.snapshot()["urls"][current.stream_url]
            self.assertEqual(record["consecutive_successes"], 1)
        self.assertEqual(self.session.head.call_count, 4)
        text = self.path.read_text()
        self.assertNotIn("private-a", text)
        self.assertNotIn("private-b", text)

    def test_shared_url_contexts_have_independent_outcomes_and_removals(self):
        good = channel("good", request_headers={"Authorization": "private-good"})
        bad = channel("bad", request_headers={"Authorization": "private-bad"})
        self.session.head.side_effect = lambda url, **kwargs: Mock(
            status_code=(
                200 if kwargs["headers"]["authorization"] == "private-good" else 404
            )
        )
        removed = Path(self.directory.name) / "removed.json"
        self.assertEqual(
            self.run_validation([good, bad], extra_removed_file=removed), [good]
        )
        first = self.snapshot()
        self.assertEqual(first["summary"]["validated_urls"], 1)
        self.assertEqual(first["summary"]["scheduling"]["validated_contexts"], 2)
        self.assertEqual(first["summary"]["validation_metrics"]["checks"], 2)
        self.assertEqual(
            first["summary"]["validation_metrics"]["http_request_attempts"], 3
        )
        self.assertEqual(set(json.loads(removed.read_text())["channels"]), {"bad"})
        self.assertEqual(
            filter_cached_offline_channels(
                [good, bad], status_file=self.path, max_age_seconds=14400
            ),
            [good],
        )
        self.run_validation([good, bad], extra_removed_file=removed)
        self.assertEqual(self.session.head.call_count, 2)
        self.assertEqual(set(json.loads(removed.read_text())["channels"]), {"bad"})
        self.assertNotIn("private-", self.path.read_text())

    def test_changed_headers_do_not_inherit_negative_cache(self):
        bad = channel(request_headers={"Authorization": "private-a"})
        good = replace(bad, request_headers={"Authorization": "private-b"})
        self.session.head.return_value.status_code = 404
        self.run_validation([bad])
        self.assertEqual(
            filter_cached_offline_channels(
                [good], status_file=self.path, max_age_seconds=14400
            ),
            [good],
        )
        self.session.head.return_value.status_code = 200
        self.assertEqual(self.run_validation([good]), [good])

    def test_other_sources_with_the_same_channel_id_do_not_remove_an_extra(self):
        extra = channel("shared-id", url="https://example.test/good.m3u8")
        other = replace(
            channel("shared-id", url="https://example.test/bad.m3u8"), source="iptv_org"
        )
        self.session.head.side_effect = lambda url, **kwargs: Mock(
            status_code=200 if url == extra.stream_url else 404
        )
        removed = Path(self.directory.name) / "removed.json"
        self.assertEqual(
            self.run_validation([extra, other], extra_removed_file=removed), [extra]
        )
        self.assertEqual(json.loads(removed.read_text())["channels"], {})

    def test_legacy_active_cache_is_rechecked_once_without_inheriting_stability(self):
        self.path.write_text(
            json.dumps(
                {
                    "urls": {
                        channel().stream_url: {
                            "active": True,
                            "checked_at": START.isoformat(),
                        }
                    }
                }
            )
        )
        self.run_validation()
        self.run_validation()
        self.assertEqual(self.session.head.call_count, 1)
        self.assertEqual(
            self.snapshot()["urls"][channel().stream_url]["consecutive_successes"], 1
        )

    def test_force_checks_fresh_transports(self):
        self.run_validation()
        self.run_validation(force=True)
        self.assertEqual(self.session.head.call_count, 2)

    def test_decreasing_remaining_ttl_does_not_invalidate_an_absolute_expiry(self):
        current = channel(
            expires_at=(START + timedelta(hours=8)).isoformat(),
            resolved_at=(START - timedelta(hours=2)).isoformat(),
            ttl_seconds=28800,
        )
        self.assertEqual(self.run_validation([current]), [current])
        self.clock.return_value += timedelta(hours=1)
        reloaded = replace(current, ttl_seconds=25200)
        self.assertEqual(self.run_validation([reloaded]), [reloaded])
        self.assertEqual(self.session.head.call_count, 1)

    def test_expiration_limits_cache_and_force_never_approves_an_expired_url(self):
        expiring = channel(expires_at=(START + timedelta(minutes=30)).isoformat())
        self.assertEqual(self.run_validation([expiring]), [expiring])
        self.clock.return_value += timedelta(minutes=30)
        self.assertEqual(self.run_validation([expiring], force=True), [])
        self.assertEqual(self.session.head.call_count, 1)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["scheduling"]["expired_contexts"], 1)
        self.assertEqual(snapshot["summary"]["validation_metrics"]["checks"], 0)

    def test_empty_catalog_is_valid(self):
        self.assertEqual(self.run_validation([]), [])
        self.assertEqual(self.snapshot()["summary"]["total_urls"], 0)
        self.session.head.assert_not_called()

    def test_declared_headers_reach_both_head_and_fallback_get(self):
        self.session.head.return_value.status_code = 405
        self.session.get.return_value.status_code = 206
        self.run_validation(
            [
                channel(
                    request_headers={
                        "Referer": "https://example.test/watch",
                        "User-Agent": "custom-client",
                    }
                )
            ]
        )
        self.assertEqual(
            self.session.head.call_args.kwargs["headers"]["referer"],
            "https://example.test/watch",
        )
        self.assertEqual(
            self.session.get.call_args.kwargs["headers"]["user-agent"], "custom-client"
        )
        self.assertEqual(
            self.session.get.call_args.kwargs["headers"]["Range"], "bytes=0-0"
        )

    def test_redirects_strip_custom_headers_only_when_origin_changes(self):
        session = StreamProbeSession()
        self.addCleanup(session.close)
        original = requests.Request("GET", "https://example.test/start").prepare()
        response = requests.Response()
        response.request = original
        for target, keep in (
            ("https://example.test/end", True),
            ("https://other.test/end", False),
            ("http://example.test/end", False),
        ):
            redirected = requests.Request(
                "GET",
                target,
                headers={
                    "Authorization": "private-value",
                    "apikey": "private-key",
                    "X-Custom": "private-custom",
                    "Referer": "https://example.test/watch",
                    "Range": "bytes=0-0",
                },
            ).prepare()
            session.rebuild_auth(redirected, response)
            self.assertEqual("apikey" in redirected.headers, keep)
            self.assertEqual("X-Custom" in redirected.headers, keep)
            self.assertEqual("Authorization" in redirected.headers, keep)
            self.assertEqual(redirected.headers["Range"], "bytes=0-0")


if __name__ == "__main__":
    unittest.main()
