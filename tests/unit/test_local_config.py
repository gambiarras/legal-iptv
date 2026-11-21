import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.local_config import local_config_dir, require_local_configuration, resource_path
from legal_iptv.sources.live_stream_catalog import fetch_channels
from legal_iptv.services.aggregate import run_aggregation


class LocalConfigurationTest(unittest.TestCase):
    def test_explicit_directory_is_effective_without_an_extra_component_suffix(self):
        with patch.dict(os.environ, {"IPTV_LOCAL_CONFIG_DIR": "/tmp/operator-inputs"}):
            self.assertEqual(local_config_dir(), Path("/tmp/operator-inputs"))

    def test_missing_configuration_fails_before_collection(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"IPTV_LOCAL_CONFIG_DIR": directory}
        ):
            output = Path(directory) / "playlist.m3u"
            output.write_text("previous valid playlist")
            config = Mock(output_path=output)
            with patch("legal_iptv.services.aggregate.HttpClient") as client:
                with self.assertRaisesRegex(RuntimeError, "Private configuration missing"):
                    run_aggregation(config)
                client.assert_not_called()
            self.assertEqual(output.read_text(), "previous valid playlist")

    def test_configured_marker_cannot_silently_substitute_empty_packaged_resources(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"IPTV_LOCAL_CONFIG_DIR": directory}
        ):
            (Path(directory) / "runtime.json").write_text('{"configured": true}')
            with self.assertRaisesRegex(RuntimeError, "Private resource configuration missing"):
                require_local_configuration()

    def test_private_resource_and_legacy_role_mapping_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"IPTV_LOCAL_CONFIG_DIR": directory}
        ):
            root = Path(directory)
            (root / "resources").mkdir()
            resource = root / "resources" / "extra_channels.json"
            resource.write_text("[]")
            self.assertEqual(resource_path("extra_channels.json"), resource)
            with self.assertRaises(ValueError):
                resource_path("../runtime.json")
            (root / "runtime.json").write_text(json.dumps({
                "source_type_aliases": {"legacy_video": "video_platform"}
            }))
            catalog = root / "channels.json"
            catalog.write_text(json.dumps([{
                "id": "example", "name": "Example Channel", "group": "kids",
                "stream_url": "https://media.example.invalid/live.m3u8",
                "status": "resolved", "source_type": "legacy_video",
                "provider_id": "legacy_video", "protocol": "legacy_video",
            }]))
            channels = fetch_channels(None, min_live_ttl=900, local_file=catalog)
            self.assertEqual(len(channels), 1)
            self.assertEqual(channels[0].source_type, "video_platform")
            self.assertEqual(channels[0].provider_id, "video_platform")
            self.assertEqual(channels[0].protocol, "video_platform")
            self.assertIn("legacy_video", catalog.read_text())
