import unittest
from datetime import datetime, timedelta, timezone

from legal_iptv.services.stream_status import offline_urls


class StreamStatusPolicyTest(unittest.TestCase):
    def test_temporary_suppression_requires_a_fresh_valid_observation(self):
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
            offline_urls(records, max_age_seconds=14400, now=now), {"fresh"}
        )


if __name__ == "__main__":
    unittest.main()
