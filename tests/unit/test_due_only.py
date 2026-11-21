import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.config import AppConfig
from legal_iptv.models import Channel
from legal_iptv.models.stream_validation import UrlValidation
from legal_iptv.services.aggregate import run_aggregation
from datetime import datetime, timezone


class DueOnlyTest(unittest.TestCase):
    def test_health_round_does_not_fetch_sources_or_epg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = Channel(id="Public.br", name="Public", logo="", group="Variedades",
                                source="public_catalog", tvg_id="Public.br",
                                stream_url="https://media.example/live.m3u8")
            (root / "catalog.json").write_text("[]")
            (root / "iptv.json").write_text(json.dumps({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "channels": [candidate.to_dict()],
            }))
            config = AppConfig(
                output_path=root / "main.m3u", meta_output_path=root / "main.meta.json",
                log_level="ERROR", min_live_ttl=900, live_catalog_file=root / "catalog.json",
                validate_streams=True, validation_max_workers=1, validation_timeout=2,
                stream_status_file=root / "status.json", stream_status_max_age=14400,
                epg_cache_file=root / "aliases.json", epg_cache_ttl_seconds=43200,
                refresh_epg_cache=False, public_catalog_cache_file=root / "iptv.json",
                public_catalog_cache_ttl_seconds=43200, refresh_public_catalog_cache=False,
                profile="full", guide_output_path=root / "guide.xml",
                extra_removed_file=root / "removed.json", validation_due_only=True,
            )
            client = Mock()
            with (patch("legal_iptv.services.aggregate.HttpClient", return_value=client),
                  patch("legal_iptv.services.aggregate.extra_channels.fetch_channels", return_value=[]),
                  patch("legal_iptv.services.link_validator.inspect_url",
                        return_value=UrlValidation(True, 200, request_count=2))):
                run_aggregation(config)
            client.get_json.assert_not_called()
            client.get_text.assert_not_called()
            client.get_bytes.assert_not_called()
            self.assertIn("Public.br", config.output_path.read_text())

    def test_missing_source_cache_fails_without_partial_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from tests.unit.test_aggregate import make_config
            from dataclasses import replace
            config = replace(make_config(directory, profile="full"), validation_due_only=True,
                             validate_streams=True)
            config.live_catalog_file.write_text("[]")
            with self.assertRaisesRegex(RuntimeError, "local source caches"):
                run_aggregation(config)
            self.assertFalse(config.output_path.exists())
