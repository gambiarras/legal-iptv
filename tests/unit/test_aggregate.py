import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.config import AppConfig
from legal_iptv.models import Channel
from legal_iptv.services.aggregate import run_aggregation
from legal_iptv.services.epg_mapper import EPGMapping, parse_xmltv_aliases
from legal_iptv.services.guide import ensure_guide


def make_config(
    temp_dir: str,
    *,
    validate_streams: bool = False,
    profile: str = "base",
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
        epg_cache_file=base_path / "epg-cache.json",
        epg_cache_ttl_seconds=43200,
        refresh_epg_cache=False,
        iptv_org_cache_file=base_path / "iptv-org-cache.json",
        iptv_org_cache_ttl_seconds=43200,
        refresh_iptv_org_cache=False,
        profile=profile,
        guide_output_path=base_path / "guide.xml",
        guide_url="https://guide.example.test/guide.xml",
        guide_diagnostics_file=base_path / "guide-diagnostics.json",
        extra_removed_file=base_path / "extra-removed.json",
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
    def test_adaptive_validation_preserves_generation_and_force_checks_again(self):
        channel = make_channel("fixture.live", "https://example.test/live.m3u8")
        session = Mock()
        session.head.return_value.status_code = 200
        now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(directory, validate_streams=True, profile="full")
            with (
                patch(
                    "legal_iptv.services.aggregate.extra_channels.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.iptv_org.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.live_stream_catalog.fetch_channels",
                    return_value=[channel],
                ),
                patch(
                    "legal_iptv.services.link_validator._get_session",
                    return_value=session,
                ),
                patch("legal_iptv.services.link_validator._utc_now", return_value=now),
            ):
                run_aggregation(config)
                first = config.output_path.read_text()
                run_aggregation(config)
                self.assertEqual(config.output_path.read_text(), first)
                status = config.stream_status_file.read_bytes()
                run_aggregation(replace(config, validate_streams=False))
                self.assertEqual(config.output_path.read_text(), first)
                self.assertEqual(config.stream_status_file.read_bytes(), status)
                self.assertEqual(session.head.call_count, 1)
                run_aggregation(replace(config, force_validation=True))
                self.assertEqual(config.output_path.read_text(), first)
                self.assertEqual(session.head.call_count, 2)

    def test_full_profile_separates_offline_discard_from_selection(self):
        preferred = make_channel("fixture.live", "https://example.test/live.m3u8")
        preferred.name = "Fixture"
        preferred.tvg_id = "Fixture.br"
        duplicate = replace(preferred, id="fixture.extra", source="extra")
        offline = make_channel("fixture.offline", "https://example.test/offline.m3u8")
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(directory, profile="full")
            with (
                patch(
                    "legal_iptv.services.aggregate.extra_channels.fetch_channels",
                    return_value=[duplicate],
                ),
                patch(
                    "legal_iptv.services.aggregate.iptv_org.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.live_stream_catalog.fetch_channels",
                    return_value=[preferred, offline],
                ),
                patch(
                    "legal_iptv.services.aggregate.filter_cached_offline_channels",
                    return_value=[preferred, duplicate],
                ),
            ):
                run_aggregation(config)
            metadata = json.loads(config.meta_output_path.read_text())
            playlist = config.output_path.read_text()

        self.assertEqual(metadata["total_input_channels"], 3)
        self.assertEqual(metadata["selected_before_stream_filter"], 3)
        self.assertEqual(metadata["selected_after_stream_filter"], 2)
        self.assertEqual(metadata["stream_filtered_channels"], 1)
        self.assertEqual(metadata["selection_filtered_channels"], 1)
        self.assertEqual(metadata["selected_channels"], 1)
        self.assertIn(preferred.stream_url, playlist)
        self.assertNotIn(offline.stream_url, playlist)

    def setUp(self):
        self.epg_patcher = patch(
            "legal_iptv.services.aggregate.load_xmltv_aliases",
            return_value={},
        )
        self.epg_patcher.start()
        self.addCleanup(self.epg_patcher.stop)

    def test_local_alias_cache_preserves_playlist_fallback_and_guide_validation(self):
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=1)).strftime("%Y%m%d%H%M%S %z")
        middle = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S %z")
        stop = (now + timedelta(hours=2)).strftime("%Y%m%d%H%M%S %z")
        guide = (
            '<tv><channel id="Fixture.local"><display-name>Fixture Local</display-name>'
            f'</channel><programme channel="Fixture.local" start="{start}" stop="{middle}">'
            '<title>Present</title></programme><programme channel="Fixture.local" '
            f'start="{middle}" stop="{stop}"><title>Future</title></programme></tv>'
        )
        catalog = [
            {
                "id": f"fixture.{name}",
                "name": f"Fixture {name}",
                "stream_url": f"https://example.test/{name}.m3u8",
                "status": "resolved",
                "source_type": "json_catalog",
                "group": "general",
            }
            for name in ("Local", "Remote")
        ]
        remote_aliases = {
            "fixture local": EPGMapping("Wrong.remote"),
            "fixture remote": EPGMapping("Fixture.remote"),
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config = replace(
                make_config(temp_dir, profile="full"), ensure_guide_enabled=True
            )
            config.guide_output_path.write_text(guide, encoding="utf-8")
            config.live_catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
            with (
                patch(
                    "legal_iptv.services.aggregate.extra_channels.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.iptv_org.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.load_xmltv_aliases",
                    return_value=remote_aliases,
                ),
                patch("legal_iptv.clients.http_client.requests.Session.get") as network,
            ):
                with (
                    patch(
                        "legal_iptv.services.aggregate.ensure_guide", wraps=ensure_guide
                    ) as validate_guide,
                    patch(
                        "legal_iptv.services.epg_mapper.parse_xmltv_aliases",
                        wraps=parse_xmltv_aliases,
                    ) as parser,
                ):
                    run_aggregation(config)
                    cold_playlist = config.output_path.read_bytes()
                    cold_meta = json.loads(config.meta_output_path.read_text())
                    run_aggregation(config)
                    warm_playlist = config.output_path.read_bytes()
                    warm_meta = json.loads(config.meta_output_path.read_text())

                self.assertEqual(parser.call_count, 1)
                self.assertEqual(validate_guide.call_count, 2)
                self.assertEqual(cold_playlist, warm_playlist)
                self.assertIn(b'tvg-id="Fixture.local"', warm_playlist)
                self.assertIn(b'tvg-id="Fixture.remote"', warm_playlist)
                self.assertNotIn(b"Wrong.remote", warm_playlist)
                self.assertEqual(config.guide_output_path.read_text(), guide)
                for meta in (cold_meta, warm_meta):
                    meta.pop("generated_at")
                    meta.pop("timings_seconds")
                self.assertEqual(cold_meta, warm_meta)

                expired = guide.replace(start, "20000101000000 +0000")
                expired = expired.replace(middle, "20000101010000 +0000")
                expired = expired.replace(stop, "20000101020000 +0000")
                config.guide_output_path.write_text(expired)
                with patch(
                    "legal_iptv.services.guide.load_epg_providers", return_value=[]
                ):
                    with self.assertRaisesRegex(RuntimeError, "No EPG provider"):
                        run_aggregation(config)
                self.assertEqual(config.output_path.read_bytes(), warm_playlist)
                network.assert_not_called()

    def test_invalid_local_guide_uses_remote_aliases_after_cache_was_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, profile="full")
            config.guide_output_path.write_text(
                '<tv><channel id="Fixture.local"><display-name>Fixture Local</display-name>'
                '</channel><programme channel="Fixture.local"/></tv>'
            )
            config.live_catalog_file.write_text(
                json.dumps(
                    [
                        {
                            "id": "fixture.local",
                            "name": "Fixture Local",
                            "stream_url": "https://example.test/local.m3u8",
                            "status": "resolved",
                            "source_type": "json_catalog",
                        }
                    ]
                )
            )
            with (
                patch(
                    "legal_iptv.services.aggregate.extra_channels.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.iptv_org.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.load_xmltv_aliases",
                    return_value={"fixture local": EPGMapping("Fixture.remote")},
                ),
            ):
                run_aggregation(config)
                self.assertIn('tvg-id="Fixture.local"', config.output_path.read_text())
                config.guide_output_path.write_text("<invalid")
                with self.assertLogs("legal_iptv.services.aggregate", level="WARNING"):
                    run_aggregation(config)
                playlist = config.output_path.read_text()

            self.assertIn('tvg-id="Fixture.remote"', playlist)
            self.assertNotIn('tvg-id="Fixture.local"', playlist)

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
        self.assertIn("timings_seconds", metadata)
        self.assertIn("total", metadata["timings_seconds"])
        self.assertEqual(metadata["epg_aliases_count"], 0)

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
        self.assertEqual(refresh_mock.call_args.kwargs["max_age_seconds"], 14400)
        self.assertEqual(refresh_mock.call_args.kwargs["failure_threshold"], 2)
        self.assertEqual(
            refresh_mock.call_args.kwargs["policy"].max_success_seconds, 86400
        )
        self.assertEqual(refresh_mock.call_args.kwargs["policy"].base_seconds, 14400)
        self.assertFalse(refresh_mock.call_args.kwargs["force"])

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
        selected_channel = make_channel(
            "same.channel", "https://example.test/live.m3u8"
        )
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

    @patch("legal_iptv.services.aggregate.live_stream_catalog.fetch_channels")
    @patch("legal_iptv.services.aggregate.iptv_org.fetch_channels")
    @patch("legal_iptv.services.aggregate.extra_channels.fetch_channels")
    @patch("legal_iptv.services.aggregate.filter_cached_offline_channels")
    def test_full_profile_writes_only_playlist_m3u_with_provider_priority(
        self,
        filter_cached_mock: Mock,
        extra_mock: Mock,
        iptv_mock: Mock,
        live_mock: Mock,
    ):
        lower = Channel(
            id="lower",
            name="Example",
            stream_url="https://lower.example/live.m3u8",
            logo="",
            group="Variedades",
            raw_group="general",
            source="iptv_org",
            provider_id="iptv_org",
            tvg_id="Example.br",
        )
        higher = Channel(
            id="higher",
            name="Example [FHD]",
            stream_url="https://higher.example/live.m3u8",
            logo="",
            group="Variedades",
            raw_group="TV Aberta",
            source="live_stream_catalog",
            source_type="stremio_addon",
            provider_id="addon_catalog_1",
            tvg_id="Example.br",
            status="resolved",
        )
        extra_mock.return_value = []
        iptv_mock.return_value = [lower]
        live_mock.return_value = [higher]
        filter_cached_mock.return_value = [lower, higher]

        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, profile="full")

            run_aggregation(config)

            playlist = config.output_path.read_text(encoding="utf-8")
            playlist_names = sorted(path.name for path in Path(temp_dir).glob("*.m3u"))

        self.assertIn("https://higher.example/live.m3u8", playlist)
        self.assertNotIn("https://lower.example/live.m3u8", playlist)
        self.assertIn('group-title="TV Aberta"', playlist)
        self.assertIn(
            'x-tvg-url="https://bit.ly/legal-epg,https://guide.example.test/guide.xml"',
            playlist,
        )
        self.assertEqual(playlist_names, ["playlist.m3u"])


if __name__ == "__main__":
    unittest.main()
