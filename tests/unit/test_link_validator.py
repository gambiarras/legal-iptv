import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.models import Channel
from legal_iptv.services.link_validator import (
    filter_active_channels,
    filter_cached_offline_channels,
    is_url_active,
    load_offline_urls,
    refresh_stream_status,
    validate_urls,
)


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


def make_channel(
    id: str,
    stream_url: str,
    *,
    source: str = "live_stream_catalog",
    source_type: str | None = None,
) -> Channel:
    return Channel(
        id=id,
        name=id,
        stream_url=stream_url,
        logo="",
        group="Web Live",
        source=source,
        source_type=source_type,
    )


class LinkValidatorTest(unittest.TestCase):
    @patch("legal_iptv.services.link_validator._get_session")
    def test_url_is_active_when_head_succeeds(self, get_session_mock: Mock):
        session = Mock()
        session.head.return_value = FakeResponse(200)
        get_session_mock.return_value = session

        self.assertTrue(is_url_active("https://example.test/live.m3u8", timeout=1))

    @patch("legal_iptv.services.link_validator._get_session")
    def test_url_uses_get_fallback_when_head_is_not_allowed(
        self,
        get_session_mock: Mock,
    ):
        session = Mock()
        session.head.return_value = FakeResponse(405)
        session.get.return_value = FakeResponse(206)
        get_session_mock.return_value = session

        self.assertTrue(is_url_active("https://example.test/live.m3u8", timeout=1))
        self.assertTrue(session.get.return_value.closed)

    @patch("legal_iptv.services.link_validator._get_session")
    def test_url_status_is_unknown_when_head_is_rate_limited(self, get_session_mock: Mock):
        session = Mock()
        session.head.return_value = FakeResponse(429)
        get_session_mock.return_value = session

        self.assertIsNone(is_url_active("https://example.test/live.m3u8", timeout=1))
        session.get.assert_not_called()

    @patch("legal_iptv.services.link_validator._get_session")
    def test_url_status_is_unknown_when_get_fallback_is_rate_limited(
        self,
        get_session_mock: Mock,
    ):
        session = Mock()
        session.head.return_value = FakeResponse(405)
        session.get.return_value = FakeResponse(429)
        get_session_mock.return_value = session

        self.assertIsNone(is_url_active("https://example.test/live.m3u8", timeout=1))
        self.assertTrue(session.get.return_value.closed)

    @patch("legal_iptv.services.link_validator._get_session")
    def test_filter_active_channels_validates_each_url_once(self, get_session_mock: Mock):
        session = Mock()
        session.head.side_effect = [
            FakeResponse(200),
            FakeResponse(404),
        ]
        get_session_mock.return_value = session
        active_url = "https://example.test/active.m3u8"
        inactive_url = "https://example.test/inactive.m3u8"
        channels = [
            make_channel("active.one", active_url),
            make_channel("active.two", active_url),
            make_channel("inactive", inactive_url),
        ]

        active_channels = filter_active_channels(
            channels,
            max_workers=1,
            timeout=1,
        )

        self.assertEqual(
            [channel.id for channel in active_channels],
            ["active.one", "active.two"],
        )
        self.assertEqual(session.head.call_count, 2)

    @patch("legal_iptv.services.link_validator.is_url_active")
    def test_validate_urls_marks_unexpected_errors_offline(self, is_url_active_mock: Mock):
        is_url_active_mock.side_effect = RuntimeError("boom")

        with self.assertLogs("legal_iptv.services.link_validator", level="WARNING"):
            status_by_url = validate_urls(
                ["https://example.test/live.m3u8"],
                max_workers=1,
                timeout=1,
            )

        self.assertEqual(status_by_url, {"https://example.test/live.m3u8": False})

    def test_loads_fresh_offline_urls_from_status_file(self):
        checked_at = datetime.now(timezone.utc).isoformat()

        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "stream-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "urls": {
                            "https://example.test/offline.m3u8": {
                                "active": False,
                                "checked_at": checked_at,
                            },
                            "https://example.test/active.m3u8": {
                                "active": True,
                                "checked_at": checked_at,
                            },
                            "https://example.test/unknown.m3u8": {
                                "active": None,
                                "checked_at": checked_at,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            offline_urls = load_offline_urls(status_file, max_age_seconds=14400)

        self.assertEqual(offline_urls, {"https://example.test/offline.m3u8"})

    def test_ignores_stale_offline_urls_from_status_file(self):
        checked_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()

        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "stream-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "urls": {
                            "https://example.test/offline.m3u8": {
                                "active": False,
                                "checked_at": checked_at,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            offline_urls = load_offline_urls(status_file, max_age_seconds=14400)

        self.assertEqual(offline_urls, set())

    def test_filters_channels_using_cached_offline_urls(self):
        checked_at = datetime.now(timezone.utc).isoformat()
        active_url = "https://example.test/active.m3u8"
        offline_url = "https://example.test/offline.m3u8"

        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "stream-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "urls": {
                            offline_url: {
                                "active": False,
                                "checked_at": checked_at,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            channels = [
                make_channel("active", active_url),
                make_channel("offline", offline_url),
            ]

            filtered_channels = filter_cached_offline_channels(
                channels,
                status_file=status_file,
                max_age_seconds=14400,
            )

        self.assertEqual([channel.id for channel in filtered_channels], ["active"])

    def test_cached_offline_filter_keeps_transient_live_catalog_urls(self):
        checked_at = datetime.now(timezone.utc).isoformat()
        transient_url = "https://manifest.googlevideo.com/live.m3u8"
        static_url = "https://example.test/offline.m3u8"

        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "stream-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "urls": {
                            transient_url: {
                                "active": False,
                                "checked_at": checked_at,
                            },
                            static_url: {
                                "active": False,
                                "checked_at": checked_at,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            channels = [
                make_channel("youtube", transient_url, source_type="youtube"),
                make_channel("static", static_url, source="extra"),
            ]

            filtered_channels = filter_cached_offline_channels(
                channels,
                status_file=status_file,
                max_age_seconds=14400,
            )

        self.assertEqual([channel.id for channel in filtered_channels], ["youtube"])

    @patch("legal_iptv.services.link_validator.validate_urls")
    def test_refresh_stream_status_writes_cache_and_filters_only_offline_channels(
        self,
        validate_urls_mock: Mock,
    ):
        active_url = "https://example.test/active.m3u8"
        offline_url = "https://example.test/offline.m3u8"
        unknown_url = "https://example.test/unknown.m3u8"
        validate_urls_mock.return_value = {
            active_url: True,
            offline_url: False,
            unknown_url: None,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "stream-status.json"
            channels = [
                make_channel("active", active_url),
                make_channel("offline", offline_url),
                make_channel("unknown", unknown_url),
            ]

            active_channels = refresh_stream_status(
                channels,
                status_file=status_file,
                max_workers=4,
                timeout=2,
                max_age_seconds=14400,
            )
            payload = json.loads(status_file.read_text(encoding="utf-8"))

        self.assertEqual([channel.id for channel in active_channels], ["active", "unknown"])
        self.assertIsInstance(payload["generated_at"], str)
        self.assertEqual(payload["summary"]["validated_urls"], 3)
        self.assertEqual(payload["summary"]["cached_urls"], 0)
        self.assertTrue(payload["urls"][active_url]["active"])
        self.assertEqual(payload["urls"][active_url]["status"], "active")
        self.assertEqual(payload["urls"][active_url]["validation"], "validated")
        self.assertEqual(payload["urls"][active_url]["sources"], ["live_stream_catalog"])
        self.assertIsInstance(payload["urls"][active_url]["checked_at"], str)
        self.assertFalse(payload["urls"][offline_url]["active"])
        self.assertEqual(payload["urls"][offline_url]["status"], "offline")
        self.assertIsInstance(payload["urls"][offline_url]["checked_at"], str)
        self.assertIsNone(payload["urls"][unknown_url]["active"])
        self.assertEqual(payload["urls"][unknown_url]["status"], "unknown")
        self.assertIsInstance(payload["urls"][unknown_url]["checked_at"], str)


    @patch("legal_iptv.services.link_validator.validate_urls")
    def test_refresh_stream_status_reuses_fresh_cached_records(
        self,
        validate_urls_mock: Mock,
    ):
        cached_url = "https://example.test/cached.m3u8"
        new_url = "https://example.test/new.m3u8"
        checked_at = datetime.now(timezone.utc).isoformat()
        validate_urls_mock.return_value = {new_url: True}

        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "stream-status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "urls": {
                            cached_url: {
                                "active": True,
                                "status": "active",
                                "checked_at": checked_at,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            channels = [
                make_channel("cached", cached_url),
                make_channel("new", new_url),
            ]

            active_channels = refresh_stream_status(
                channels,
                status_file=status_file,
                max_workers=4,
                timeout=2,
                max_age_seconds=14400,
            )
            payload = json.loads(status_file.read_text(encoding="utf-8"))

        validate_urls_mock.assert_called_once_with(
            [new_url],
            max_workers=4,
            timeout=2,
        )
        self.assertEqual([channel.id for channel in active_channels], ["cached", "new"])
        self.assertEqual(payload["summary"]["validated_urls"], 1)
        self.assertEqual(payload["summary"]["cached_urls"], 1)
        self.assertEqual(payload["urls"][cached_url]["validation"], "cached")
        self.assertEqual(payload["urls"][new_url]["validation"], "validated")

    @patch("legal_iptv.services.link_validator.validate_urls")
    def test_refresh_stream_status_marks_transient_live_failures_as_unknown(
        self,
        validate_urls_mock: Mock,
    ):
        transient_url = "https://manifest.googlevideo.com/live.m3u8"
        validate_urls_mock.return_value = {transient_url: False}

        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "stream-status.json"
            channels = [
                make_channel("youtube", transient_url, source_type="youtube"),
            ]

            active_channels = refresh_stream_status(
                channels,
                status_file=status_file,
                max_workers=4,
                timeout=2,
                max_age_seconds=14400,
            )
            payload = json.loads(status_file.read_text(encoding="utf-8"))

        self.assertEqual([channel.id for channel in active_channels], ["youtube"])
        self.assertIsNone(payload["urls"][transient_url]["active"])
        self.assertEqual(payload["urls"][transient_url]["status"], "unknown")
        self.assertIsInstance(payload["urls"][transient_url]["checked_at"], str)


if __name__ == "__main__":
    unittest.main()
