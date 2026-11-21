import unittest
from datetime import datetime, timedelta, timezone

from legal_iptv.services.stream_status import offline_urls, retain_rejected_records


class StreamStatusPolicyTest(unittest.TestCase):
    def test_rejection_survives_retry_deadline_and_requires_valid_timestamp(self):
        now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        values = {
            "fresh": now - timedelta(minutes=1),
            "expired": now - timedelta(hours=5),
            "future": now + timedelta(hours=1),
            "missing": None,
            "malformed": 123,
        }
        records = {
            key: {
                "active": False,
                "status": "temporarily_unavailable",
                "checked_at": (
                    value.isoformat() if isinstance(value, datetime) else value
                ),
            }
            for key, value in values.items()
        }

        self.assertEqual(
            offline_urls(records, max_age_seconds=14400, now=now), {"fresh", "expired"}
        )

    def test_context_budget_refuses_to_forget_rejections(self):
        now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        previous = {f"https://example.test/{index}": {"active": False, "status": "offline", "checked_at": now.isoformat()} for index in range(10001)}
        with self.assertRaisesRegex(ValueError, "budget"):
            retain_rejected_records(previous, {}, now=now)


if __name__ == "__main__":
    unittest.main()
