import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from legal_iptv.models.stream_validation import UrlValidation
from legal_iptv.services.validation_metrics import (
    build_validation_metrics,
    safe_metric_label,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
URL = "https://example.test/live.m3u8"


def metadata(provider="provider-a", logical="channel-a", feed="sp", name="Channel A"):
    return {
        "channels": [
            {
                "id": "variant-a",
                "name": name,
                "source": "live_stream_catalog",
                "source_type": "json_catalog",
                "provider_id": provider,
                "logical_channel_id": logical,
                "feed_id": feed,
            }
        ]
    }


def run_metrics(previous=None, results=None, mapping=None, now=NOW, **limits):
    return build_validation_metrics(
        previous or {},
        results or {},
        mapping or {URL: metadata()},
        now=now,
        cached_urls=0,
        suppressed_urls=0,
        **limits,
    )


class ValidationMetricsTest(unittest.TestCase):
    def test_metric_labels_reject_objects_and_credential_shaped_text(self):
        for value in (
            "Authorization: Bearer SAMPLE_TEST_VALUE",
            "Bearer SAMPLE_TEST_VALUE",
            "X-Api-Key: SAMPLE_TEST_VALUE",
            "token=SAMPLE_TEST_VALUE",
        ):
            self.assertEqual(safe_metric_label(value), "[redacted]")
        self.assertEqual(
            safe_metric_label({"password": "SAMPLE_TEST_VALUE"}), "[invalid]"
        )

    def test_unrepresentable_history_timezone_is_rejected(self):
        first = run_metrics(results={URL: UrlValidation(True, 200)})
        history = copy.deepcopy(first.history)
        for series in history["channels"].values():
            series["first_checked_at"] = "0001-01-01T00:00:00+01:00"
        result = run_metrics(previous={"history": history})
        self.assertEqual(result.history["channels"], {})

    def test_counts_real_observations_and_measures_http_attempts(self):
        metrics = run_metrics(
            results={URL: UrlValidation(True, 200, duration_ms=25, request_count=2)}
        )
        self.assertEqual(metrics.summary["checks"], 1)
        self.assertEqual(metrics.summary["successful_checks"], 1)
        self.assertEqual(metrics.summary["http_request_attempts"], 2)
        self.assertEqual(metrics.summary["mean_duration_ms"], 25)
        self.assertEqual(metrics.summary["http_status_counts"], {"200": 1})
        self.assertEqual(metrics.top_failing_channels, [])

    def test_cache_hit_does_not_invent_observations_or_refresh_history_timestamp(self):
        first = run_metrics(results={URL: UrlValidation(True, 200)})
        second = build_validation_metrics(
            {"history": first.history},
            {},
            {URL: metadata()},
            now=NOW + timedelta(hours=1),
            cached_urls=1,
            suppressed_urls=0,
        )
        self.assertEqual(second.history, first.history)
        self.assertEqual(second.summary["checks"], 0)
        self.assertEqual(second.summary["cache_hit_rate"], 1)

    def test_legacy_snapshot_does_not_become_fabricated_history(self):
        legacy = {"urls": {URL: {"active": False, "consecutive_failures": 50}}}
        first = run_metrics(previous=legacy)
        self.assertEqual(first.history["channels"], {})
        measured = run_metrics(previous=legacy, results={URL: UrlValidation(None, 503)})
        self.assertEqual(measured.providers[0]["checks"], 1)
        self.assertEqual(measured.providers[0]["unknown_checks"], 1)

    def test_rotation_keeps_channel_history_separate_from_exact_url_history(self):
        first = run_metrics(results={URL: UrlValidation(False, 404)})
        rotated = "https://new.example.test/live.m3u8"
        updated_metadata = metadata()
        updated_metadata["channels"][0]["id"] = "url-dependent-new-variant"
        second = run_metrics(
            previous={"history": first.history},
            results={rotated: UrlValidation(True, 200)},
            mapping={rotated: updated_metadata},
            now=NOW + timedelta(hours=1),
        )
        self.assertEqual(len(second.history["transports"]), 2)
        self.assertEqual(len(second.history["channels"]), 1)
        self.assertEqual(second.providers[0]["checks"], 2)
        self.assertEqual(second.top_failing_channels[0]["checks"], 2)
        self.assertEqual(second.top_failing_channels[0]["confirmed_offline_rate"], 0.5)

    def test_shared_url_is_one_check_with_deduplicated_attribution(self):
        first = metadata()
        channels = (
            first["channels"] * 2
            + metadata(feed="rj")["channels"]
            + metadata(provider="provider-b")["channels"]
        )
        metrics = run_metrics(
            results={URL: UrlValidation(False, 404)},
            mapping={URL: {"channels": channels}},
        )
        self.assertEqual(metrics.summary["checks"], 1)
        self.assertEqual(len(metrics.history["channels"]), 3)
        self.assertEqual(len(metrics.providers), 2)
        self.assertTrue(all(provider["checks"] == 1 for provider in metrics.providers))

    def test_unknown_observations_are_not_offline_measurements(self):
        first = run_metrics(results={URL: UrlValidation(None, error_type="Timeout")})
        second = run_metrics(
            previous={"history": first.history},
            results={URL: UrlValidation(None, 503)},
            now=NOW + timedelta(seconds=1),
        )
        channel = second.top_failing_channels[0]
        self.assertEqual(channel["unknown_checks"], 2)
        self.assertEqual(channel["offline_checks"], 0)
        self.assertEqual(channel["unsuccessful_rate"], 1)
        self.assertEqual(channel["confirmed_offline_rate"], 0)

    def test_summary_and_history_do_not_expose_urls_headers_or_free_errors(self):
        secret = "sensitive-value"
        private_url = f"https://example.test/live?token={secret}"
        details = metadata(
            provider=f"https://example.test/?token={secret}", name=private_url
        )
        details["channels"][0]["headers"] = {"Authorization": secret}
        metrics = run_metrics(
            results={private_url: UrlValidation(None, error_type=private_url)},
            mapping={private_url: details},
        )
        serialized = json.dumps(
            {
                "history": metrics.history,
                "summary": metrics.summary,
                "channels": metrics.top_failing_channels,
                "providers": metrics.providers,
            }
        )
        self.assertNotIn(secret, serialized)
        self.assertNotIn("https://", serialized)
        self.assertEqual(metrics.summary["error_type_counts"], {"OtherError": 1})

    def test_inactive_series_expire(self):
        first = run_metrics(results={URL: UrlValidation(False, 404)})
        expired = run_metrics(
            previous={"history": first.history}, now=NOW + timedelta(days=31)
        )
        self.assertTrue(
            all(
                not expired.history[kind]
                for kind in ("channels", "providers", "transports")
            )
        )
        self.assertEqual(expired.top_failing_channels, [])

    def test_series_and_recent_events_have_independent_limits(self):
        history = {}
        for day in range(4):
            metrics = run_metrics(
                previous={"history": history},
                results={URL: UrlValidation(None, 503)},
                now=NOW + timedelta(days=day),
                max_events=2,
            )
            history = metrics.history
        channel = next(iter(history["channels"].values()))
        self.assertEqual(channel["checks"], 4)
        self.assertEqual(len(channel["recent_events"]), 2)
        self.assertEqual(channel["first_checked_at"], NOW.isoformat())

        capped = run_metrics(
            results={f"{URL}/{i}": UrlValidation(False, 404) for i in range(5)},
            mapping={
                f"{URL}/{i}": metadata(logical=str(i), provider=str(i))
                for i in range(5)
            },
            max_series=2,
        )
        self.assertTrue(
            all(
                len(capped.history[kind]) == 2
                for kind in ("channels", "providers", "transports")
            )
        )
        self.assertEqual(capped.summary["checks"], 5)

    def test_recent_events_expire_while_lifetime_counts_remain_explicit(self):
        first = run_metrics(results={URL: UrlValidation(False, 404)})
        second = run_metrics(
            previous={"history": first.history},
            results={URL: UrlValidation(None, 503)},
            now=NOW + timedelta(days=29),
        )
        third = run_metrics(
            previous={"history": second.history}, now=NOW + timedelta(days=31)
        )
        series = next(iter(third.history["channels"].values()))
        self.assertEqual(series["checks"], 2)
        self.assertEqual(len(series["recent_events"]), 1)
        self.assertEqual(third.history["counter_scope"], "retained_series_lifetime")

    def test_corrupt_history_can_be_rebuilt_without_using_legacy_status_as_events(self):
        first = run_metrics(results={URL: UrlValidation(False, 404)})
        damaged = copy.deepcopy(first.history)
        for kind in ("channels", "providers", "transports"):
            for series in damaged[kind].values():
                series["checks"] = "broken"
        for history in ([], {"channels": []}, damaged):
            with self.subTest(history=type(history).__name__):
                metrics = run_metrics(
                    previous={"history": history, "urls": {URL: {"active": True}}},
                    results={URL: UrlValidation(True, 200)},
                )
                self.assertEqual(metrics.providers[0]["checks"], 1)
                self.assertEqual(metrics.providers[0]["successful_checks"], 1)

    def test_invalid_latency_is_not_reported_as_measured(self):
        metrics = run_metrics(
            results={URL: UrlValidation(None, duration_ms=float("nan"))}
        )
        self.assertEqual(metrics.summary["timed_checks"], 0)
        self.assertIsNone(metrics.summary["mean_duration_ms"])


if __name__ == "__main__":
    unittest.main()
