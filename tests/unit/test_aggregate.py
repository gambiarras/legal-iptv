import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.config import AppConfig
from legal_iptv.models import Channel
from legal_iptv.services.aggregate import run_aggregation


def make_config(
    temp_dir: str,
    *,
    validate_streams: bool = False,
) -> AppConfig:
    base_path = Path(temp_dir)
    return AppConfig(
        output_path=base_path / "playlist.m3u",
        meta_output_path=base_path / "playlist.meta.json",
        log_level="INFO",
        min_live_ttl=900,
        live_catalog_file=base_path / "channels.json",
        validate_streams=validate_streams,
        validation_max_workers=4,
        validation_timeout=2,
        stream_status_file=base_path / "stream-status.json",
        stream_status_max_age=14400,
    )


def make_channel(id: str, stream_url: str) -> Channel:
    return Channel(
        id=id,
        name=id,
        stream_url=stream_url,
        logo="",
        group="Web Live",
        source="live_stream_catalog",
        status="resolved",
    )


class AggregateTest(unittest.TestCase):
    def setUp(self):
        self.epg_patcher = patch(
            "legal_iptv.services.aggregate.load_xmltv_aliases",
            return_value={},
        )
        self.epg_patcher.start()
        self.addCleanup(self.epg_patcher.stop)

    @patch("legal_iptv.services.aggregate.live_stream_catalog.fetch_channels")
    @patch("legal_iptv.services.aggregate.iptv_org.fetch_channels")
    @patch("legal_iptv.services.aggregate.extra_channels.fetch_channels")
    @patch("legal_iptv.services.aggregate.filter_cached_offline_channels")
    def test_run_aggregation_filters_cached_offline_channels(
        self,
        filter_cached_mock: Mock,
        extra_mock: Mock,
        iptv_mock: Mock,
        live_mock: Mock,
    ):
        channel = make_channel("live.channel", "https://example.test/live.m3u8")
        extra_mock.return_value = []
        iptv_mock.return_value = []
        live_mock.return_value = [channel]
        filter_cached_mock.return_value = [channel]

        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir)

            run_aggregation(config)

            playlist = config.output_path.read_text(encoding="utf-8")
            metadata = json.loads(config.meta_output_path.read_text(encoding="utf-8"))

        filter_cached_mock.assert_called_once()
        self.assertIn("https://example.test/live.m3u8", playlist)
        self.assertEqual(metadata["total_input_channels"], 1)
        self.assertEqual(metadata["selected_channels"], 1)
        self.assertEqual(metadata["selected_before_stream_filter"], 1)
        self.assertEqual(metadata["stream_filtered_channels"], 0)
        self.assertEqual(metadata["deduplicated_channels"], 0)
        self.assertEqual(metadata["source_errors"], {})
        self.assertEqual(metadata["selected_by_source"], {"live_stream_catalog": 1})

    @patch("legal_iptv.services.aggregate.live_stream_catalog.fetch_channels")
    @patch("legal_iptv.services.aggregate.iptv_org.fetch_channels")
    @patch("legal_iptv.services.aggregate.extra_channels.fetch_channels")
    @patch("legal_iptv.services.aggregate.refresh_stream_status")
    def test_run_aggregation_refreshes_stream_status_when_enabled(
        self,
        refresh_mock: Mock,
        extra_mock: Mock,
        iptv_mock: Mock,
        live_mock: Mock,
    ):
        channel = make_channel("live.channel", "https://example.test/live.m3u8")
        extra_mock.return_value = []
        iptv_mock.return_value = []
        live_mock.return_value = [channel]
        refresh_mock.return_value = [channel]

        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, validate_streams=True)

            run_aggregation(config)

        refresh_mock.assert_called_once()

    @patch("legal_iptv.services.aggregate.live_stream_catalog.fetch_channels")
    @patch("legal_iptv.services.aggregate.iptv_org.fetch_channels")
    @patch("legal_iptv.services.aggregate.extra_channels.fetch_channels")
    @patch("legal_iptv.services.aggregate.filter_cached_offline_channels")
    def test_run_aggregation_continues_when_source_fails(
        self,
        filter_cached_mock: Mock,
        extra_mock: Mock,
        iptv_mock: Mock,
        live_mock: Mock,
    ):
        channel = make_channel("live.channel", "https://example.test/live.m3u8")
        extra_mock.side_effect = RuntimeError("extra failed")
        iptv_mock.return_value = []
        live_mock.return_value = [channel]
        filter_cached_mock.return_value = [channel]

        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir)

            with self.assertLogs("legal_iptv.services.aggregate", level="ERROR"):
                run_aggregation(config)

            playlist = config.output_path.read_text(encoding="utf-8")
            metadata = json.loads(config.meta_output_path.read_text(encoding="utf-8"))

        self.assertIn("https://example.test/live.m3u8", playlist)
        self.assertEqual(metadata["total_input_channels"], 1)
        self.assertEqual(metadata["selected_channels"], 1)
        self.assertEqual(metadata["source_errors"], {"extra": "extra failed"})

    @patch("legal_iptv.services.aggregate.live_stream_catalog.fetch_channels")
    @patch("legal_iptv.services.aggregate.iptv_org.fetch_channels")
    @patch("legal_iptv.services.aggregate.extra_channels.fetch_channels")
    @patch("legal_iptv.services.aggregate.filter_cached_offline_channels")
    def test_run_aggregation_reports_deduplication_and_stream_filter_counts(
        self,
        filter_cached_mock: Mock,
        extra_mock: Mock,
        iptv_mock: Mock,
        live_mock: Mock,
    ):
        duplicate_low_priority = Channel(
            id="same.channel",
            name="Same Channel",
            stream_url="https://example.test/live.m3u8",
            logo="",
            group="Web Live",
            source="iptv_org",
            status=None,
        )
        selected_channel = make_channel("same.channel", "https://example.test/live.m3u8")
        extra_mock.return_value = []
        iptv_mock.return_value = [duplicate_low_priority]
        live_mock.return_value = [selected_channel]
        filter_cached_mock.return_value = []

        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir)

            run_aggregation(config)

            metadata = json.loads(config.meta_output_path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["total_input_channels"], 2)
        self.assertEqual(metadata["selected_before_stream_filter"], 1)
        self.assertEqual(metadata["stream_filtered_channels"], 1)
        self.assertEqual(metadata["deduplicated_channels"], 1)
        self.assertEqual(metadata["selected_channels"], 0)


if __name__ == "__main__":
    unittest.main()
