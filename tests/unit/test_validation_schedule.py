import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from legal_iptv.models import Channel
from legal_iptv.services.validation_schedule import (
    ValidationPolicy,
    can_reuse,
    context_for_channel,
    matching_record,
    previous_observation,
    schedule_record,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def channel(**kwargs):
    return Channel(
        "test", "Test", "https://example.test/live.m3u8", "", "News", "extra", **kwargs
    )


class ValidationScheduleTest(unittest.TestCase):
    def setUp(self):
        self.policy = ValidationPolicy()
        self.context = context_for_channel(channel())

    def record(self, active=True, previous=None, **kwargs):
        return schedule_record(
            {
                "active": (
                    False
                    if kwargs.get("status") == "temporarily_unavailable"
                    else active
                ),
                "status": (
                    "active"
                    if active is True
                    else "offline" if active is False else "unknown"
                ),
                "checked_at": NOW.isoformat(),
                "consecutive_failures": 0 if active is True else 1,
                **kwargs,
            },
            previous,
            self.context,
            self.policy,
            now=NOW,
            active=active,
        )

    def test_intervals_grow_with_success_and_are_bounded(self):
        self.assertEqual(
            [self.policy.interval(True, n, transient=False) for n in range(1, 7)],
            [14400, 28800, 43200, 86400, 86400, 86400],
        )
        self.assertEqual(self.policy.interval(True, 1000000, transient=True), 14400)
        self.assertEqual(
            ValidationPolicy(max_success_seconds=14400).interval(
                True, 4, transient=False
            ),
            14400,
        )

    def test_retry_backoff_never_exceeds_base_or_overflows(self):
        self.assertEqual(
            [self.policy.interval(False, n, transient=False) for n in range(1, 7)],
            [900, 1800, 3600, 7200, 14400, 14400],
        )
        self.assertEqual(self.policy.interval(False, 10**100, transient=False), 14400)

    def test_invalid_policy_limits_are_rejected(self):
        for field in ("base_seconds", "max_success_seconds", "retry_seconds"):
            for value in (0, -1, 10**100):
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(ValueError),
                ):
                    ValidationPolicy(**{field: value})

    def test_context_is_case_insensitive_but_credentials_and_inputs_invalidate(self):
        first = channel(
            request_headers={
                "Referer": "https://example.test/",
                "Authorization": "private-a",
            }
        )
        equivalent = replace(
            first,
            request_headers={
                "authorization": "private-a",
                "referer": "https://example.test/",
            },
        )
        self.assertEqual(
            context_for_channel(first).id, context_for_channel(equivalent).id
        )
        for changed in (
            replace(first, request_headers={"Authorization": "private-b"}),
            replace(first, stream_url=first.stream_url + "?v=2"),
            replace(first, resolution="1080p"),
            replace(first, codec="hevc"),
            replace(first, expires_at=(NOW + timedelta(hours=1)).isoformat()),
            replace(first, secret_refs={"auth": "reference-b"}),
        ):
            self.assertNotEqual(
                context_for_channel(first).id, context_for_channel(changed).id
            )

    def test_only_declared_ttl_uses_resolution_time(self):
        self.assertEqual(
            context_for_channel(channel()).id,
            context_for_channel(channel(resolved_at=NOW.isoformat())).id,
        )
        resolved = channel(resolved_at=NOW.isoformat(), ttl_seconds=1800)
        self.assertEqual(
            context_for_channel(resolved).expires_at, NOW + timedelta(minutes=30)
        )
        self.assertNotEqual(
            context_for_channel(resolved).id,
            context_for_channel(
                replace(resolved, resolved_at=(NOW + timedelta(minutes=1)).isoformat())
            ).id,
        )

    def test_expiry_is_conservative_for_invalid_and_overflowing_inputs(self):
        for values in (
            {"expires_at": "bad"},
            {"ttl_seconds": 30},
            {"resolved_at": NOW.isoformat(), "ttl_seconds": 10**100},
            {"expires_at": NOW.isoformat()},
        ):
            self.assertTrue(context_for_channel(channel(**values)).expired(NOW))
        shortest = context_for_channel(
            channel(
                expires_at=(NOW + timedelta(hours=1)).isoformat(),
                resolved_at=NOW.isoformat(),
                ttl_seconds=600,
            )
        )
        self.assertEqual(shortest.expires_at, NOW + timedelta(hours=1))
        self.assertFalse(shortest.expired(NOW))

    def test_absolute_expiry_is_authoritative_and_remaining_ttl_is_not_identity(self):
        original = channel(
            expires_at=(NOW + timedelta(hours=4)).isoformat(),
            resolved_at=(NOW - timedelta(hours=2)).isoformat(),
            ttl_seconds=14400,
        )
        reloaded = replace(original, ttl_seconds=7200)
        self.assertEqual(
            context_for_channel(original).id, context_for_channel(reloaded).id
        )
        self.assertEqual(
            context_for_channel(reloaded).expires_at, NOW + timedelta(hours=4)
        )

    def test_transient_sources_are_identified_explicitly(self):
        self.assertTrue(
            context_for_channel(
                replace(channel(), source="live_stream_catalog", source_type="youtube")
            ).transient
        )
        self.assertFalse(context_for_channel(channel(source_type="youtube")).transient)

    def test_missing_legacy_or_other_context_is_not_reused(self):
        self.assertIsNone(matching_record(None, self.context))
        self.assertIsNone(matching_record({"active": True}, self.context))
        record = self.record()
        self.assertEqual(
            matching_record({"contexts": {self.context.id: record}}, self.context),
            record,
        )
        self.assertIsNone(
            matching_record({"contexts": {self.context.id: "bad"}}, self.context)
        )
        self.assertFalse(can_reuse(None, self.context, self.policy, now=NOW))
        self.assertFalse(
            can_reuse(
                {**record, "context_id": "different"},
                self.context,
                self.policy,
                now=NOW,
            )
        )

    def test_due_boundary_and_force_do_not_reuse(self):
        record = self.record()
        self.assertTrue(can_reuse(record, self.context, self.policy, now=NOW))
        self.assertFalse(
            can_reuse(record, self.context, self.policy, now=NOW, force=True)
        )
        self.assertTrue(
            can_reuse(
                record,
                self.context,
                self.policy,
                now=NOW + timedelta(hours=4, microseconds=-1),
            )
        )
        self.assertFalse(
            can_reuse(record, self.context, self.policy, now=NOW + timedelta(hours=4))
        )

    def test_malformed_future_or_inflated_schedule_is_due(self):
        base = self.record()
        for overrides in (
            {"checked_at": None},
            {"checked_at": 123},
            {"checked_at": (NOW + timedelta(seconds=1)).isoformat()},
            {"next_check_at": None},
            {"next_check_at": "bad"},
            {"next_check_at": NOW.isoformat()},
            {"next_check_at": (NOW + timedelta(days=90)).isoformat()},
            {"consecutive_successes": True},
            {"consecutive_successes": -1},
            {"last_probe_active": "true"},
            {"schedule_version": 999},
        ):
            with self.subTest(overrides=overrides):
                self.assertFalse(
                    can_reuse({**base, **overrides}, self.context, self.policy, now=NOW)
                )
        del base["last_probe_active"]
        self.assertFalse(can_reuse(base, self.context, self.policy, now=NOW))

    def test_incomplete_or_contradictory_observations_are_never_reused(self):
        base = self.record()
        missing = dict(base)
        del missing["active"]
        for malformed in (
            missing,
            {**base, "active": "true"},
            {**base, "active": False},
            {**base, "status": "offline"},
            {**base, "last_probe_active": False},
            {**base, "last_probe_active": "bad"},
        ):
            self.assertFalse(can_reuse(malformed, self.context, self.policy, now=NOW))
            self.assertIsNone(
                previous_observation(malformed, self.context, self.policy, now=NOW)
            )

    def test_success_schedule_stops_at_expiration(self):
        expiring = context_for_channel(
            channel(expires_at=(NOW + timedelta(minutes=10)).isoformat())
        )
        record = schedule_record(
            {
                "active": True,
                "status": "active",
                "checked_at": NOW.isoformat(),
                "consecutive_failures": 0,
            },
            None,
            expiring,
            self.policy,
            now=NOW,
            active=True,
        )
        self.assertEqual(
            record["next_check_at"], (NOW + timedelta(minutes=10)).isoformat()
        )
        self.assertTrue(can_reuse(record, expiring, self.policy, now=NOW))
        self.assertFalse(
            can_reuse(record, expiring, self.policy, now=NOW + timedelta(minutes=10))
        )

    def test_negative_schedule_uses_raw_result_and_preserves_suppression(self):
        record = self.record(None, status="temporarily_unavailable")
        self.assertEqual(record["consecutive_successes"], 0)
        self.assertEqual(
            record["next_check_at"], (NOW + timedelta(minutes=15)).isoformat()
        )
        self.assertTrue(can_reuse(record, self.context, self.policy, now=NOW))
        self.assertFalse(
            can_reuse(
                record, self.context, self.policy, now=NOW + timedelta(minutes=15)
            )
        )

    def test_streak_is_reset_for_context_change_and_abandoned_or_future_records(self):
        old = {**self.record(), "consecutive_successes": 3}
        self.assertEqual(self.record(previous=old)["consecutive_successes"], 4)
        for change in (
            {"context_id": "other"},
            {"checked_at": "bad"},
            {"checked_at": (NOW + timedelta(minutes=1)).isoformat()},
            {"checked_at": (NOW - timedelta(days=2)).isoformat()},
        ):
            with self.subTest(change=change):
                previous = {**old, **change}
                self.assertIsNone(
                    previous_observation(previous, self.context, self.policy, now=NOW)
                )
                self.assertEqual(
                    self.record(previous=previous)["consecutive_successes"], 1
                )
        self.assertEqual(
            self.record(previous={**old, "consecutive_successes": "bad"})[
                "consecutive_successes"
            ],
            1,
        )

    def test_delayed_job_rechecks_but_keeps_the_observed_stability(self):
        previous = {
            **self.record(),
            "consecutive_successes": 4,
            "next_check_at": (NOW + timedelta(hours=24)).isoformat(),
        }
        later = NOW + timedelta(hours=24, minutes=1)
        self.assertFalse(can_reuse(previous, self.context, self.policy, now=later))
        self.assertIsNotNone(
            previous_observation(previous, self.context, self.policy, now=later)
        )
        updated = schedule_record(
            {
                "active": True,
                "status": "active",
                "checked_at": later.isoformat(),
                "consecutive_failures": 0,
            },
            previous,
            self.context,
            self.policy,
            now=later,
            active=True,
        )
        self.assertEqual(updated["consecutive_successes"], 4)
        self.assertEqual(
            updated["next_check_at"], (later + timedelta(hours=24)).isoformat()
        )


if __name__ == "__main__":
    unittest.main()
