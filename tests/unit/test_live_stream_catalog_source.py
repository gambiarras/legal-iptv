import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.exporters import render_m3u
from legal_iptv.sources.live_stream_catalog import LIVE_STREAM_CATALOG_URL, fetch_channels


class FakeClient:
    def __init__(self, payload=None):
        self.payload = payload or []
        self.requested_urls: list[str] = []

    def get_json(self, url: str):
        self.requested_urls.append(url)
        return self.payload


class LiveStreamCatalogSourceTest(unittest.TestCase):
    def test_extensionless_addon_stream_reaches_full_playlist(self):
        payload = [
            {
                "id": f"addon.channel.{quality.lower()}",
                "name": f"Example Channel [{quality}]",
                "stream_url": f"https://media.example.test/relay/{quality.lower()}",
                "source_type": "stremio_addon",
                "provider_id": "addon_catalog_1",
                "logical_channel_id": "addon.channel",
                "variant_label": quality,
                "group": "general",
                "status": "resolved",
                "protocol": "https",
                "requires_dynamic_resolution": False,
                "publishable_static": True,
                "ttl_seconds": None,
            }
            for quality in ("FHD", "SD")
        ]
        configuration = load_playlist_configuration()
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "channels.json"
            catalog_path.write_text(json.dumps(payload), encoding="utf-8")
            client = FakeClient()

            channels = fetch_channels(client, min_live_ttl=900, local_file=catalog_path)
            selected = select_profile_channels(channels, configuration, "full")
            base = select_profile_channels(channels, configuration, "base")

        self.assertEqual(client.requested_urls, [])
        self.assertEqual(len(channels), 2)
        self.assertEqual([item.id for item in selected], ["addon.channel.fhd"])
        self.assertEqual(base, [])
        for player_profile in ("portable", "kodi"):
            with self.subTest(player_profile=player_profile):
                playlist = render_m3u(
                    selected,
                    player_profile=player_profile,
                    category_order=configuration.group_order,
                    enforce_capabilities=True,
                )

                self.assertEqual(playlist.count("#EXTINF:"), 1)
                self.assertIn("https://media.example.test/relay/fhd\n", playlist)
                self.assertNotIn("https://media.example.test/relay/sd", playlist)

    def test_full_playlist_keeps_excluding_restricted_addon_streams(self):
        configuration = load_playlist_configuration()
        restrictions = {
            "dynamic": {"requires_dynamic_resolution": True},
            "not_publishable": {"publishable_static": False},
            "drm": {"drm": {"type": "widevine"}},
            "cookie": {"request_headers": {"Cookie": "session=private"}},
            "ssai": {"delivery_mode": "ssai"},
        }
        for name, restriction in restrictions.items():
            with self.subTest(restriction=name):
                client = FakeClient([{
                    "id": "addon.channel",
                    "name": "Example Channel",
                    "stream_url": "https://media.example.test/relay/channel",
                    "source_type": "stremio_addon",
                    "status": "resolved",
                    "publishable_static": True,
                    **restriction,
                }])

                channels = fetch_channels(client, min_live_ttl=900)
                selected = select_profile_channels(channels, configuration, "full")

                self.assertEqual(len(channels), 1)
                self.assertEqual(selected, [])
                self.assertNotIn(
                    "#EXTINF:",
                    render_m3u(selected, enforce_capabilities=True),
                )

    def test_loads_usable_channels_from_local_catalog(self):
        payload = [
            {
                "id": "channel.resolved.nullttl",
                "name": "Resolved Null TTL",
                "stream_url": "https://example.test/live.m3u8",
                "logo": "https://example.test/logo.png",
                "group": "web",
                "source_type": "twitch",
                "source_url": "https://www.twitch.tv/example",
                "tvg_id": "ResolvedNullTTL.br",
                "status": "resolved",
                "ttl_seconds": None,
            },
            {
                "id": "channel.lowttl",
                "name": "Low TTL",
                "stream_url": "https://example.test/low.m3u8",
                "group": "web",
                "status": "resolved",
                "ttl_seconds": 100,
            },
            {
                "id": "channel.offline",
                "name": "Offline",
                "stream_url": "https://example.test/offline.m3u8",
                "group": "web",
                "status": "offline",
                "ttl_seconds": None,
            },
            {
                "id": "channel.missing.stream",
                "name": "Missing Stream",
                "group": "web",
                "status": "resolved",
                "ttl_seconds": None,
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "channels.json"
            catalog_path.write_text(json.dumps(payload), encoding="utf-8")

            channels = fetch_channels(
                FakeClient(),
                min_live_ttl=900,
                local_file=catalog_path,
            )

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].id, "channel.resolved.nullttl")
        self.assertEqual(channels[0].tvg_id, "ResolvedNullTTL.br")
        self.assertEqual(channels[0].group, "Web Live")
        self.assertIsNone(channels[0].ttl_seconds)

    def test_does_not_filter_kick_channels_by_platform_ttl(self):
        payload = [
            {
                "id": "kick.channel",
                "name": "Kick Channel",
                "stream_url": "https://kick.example.test/live.m3u8",
                "group": "web",
                "source_type": "kick",
                "status": "resolved",
                "ttl_seconds": 598,
            },
            {
                "id": "youtube.channel",
                "name": "YouTube Channel",
                "stream_url": "https://youtube.example.test/live.m3u8",
                "group": "web",
                "source_type": "youtube",
                "status": "resolved",
                "ttl_seconds": 598,
            },
            {
                "id": "kick.expiring",
                "name": "Kick Expiring",
                "stream_url": "https://kick.example.test/expiring.m3u8",
                "group": "web",
                "source_type": "kick",
                "status": "resolved",
                "ttl_seconds": 30,
            },
        ]
        client = FakeClient(payload)

        channels = fetch_channels(client, min_live_ttl=900)

        self.assertEqual([channel.id for channel in channels], ["kick.channel", "kick.expiring"])
        self.assertEqual(channels[0].ttl_seconds, 598)
        self.assertEqual(channels[1].ttl_seconds, 30)

    def test_local_catalog_file_is_required_when_configured(self):
        missing_path = Path("/tmp/legal_iptv_missing_channels.json")
        client = FakeClient()

        with self.assertRaises(FileNotFoundError):
            fetch_channels(client, min_live_ttl=900, local_file=missing_path)

        self.assertEqual(client.requested_urls, [])

    def test_loads_remote_catalog_when_local_file_is_not_configured(self):
        payload = [
            {
                "id": "remote.channel",
                "name": "Remote Channel",
                "url": "https://example.test/remote.m3u8",
                "group": "web",
                "status": "resolved",
                "ttl_seconds": 1200,
            }
        ]
        client = FakeClient(payload)

        channels = fetch_channels(client, min_live_ttl=900)

        self.assertEqual(client.requested_urls, [LIVE_STREAM_CATALOG_URL])
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].stream_url, "https://example.test/remote.m3u8")

    def test_recalculates_ttl_from_expires_at(self):
        future_expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        past_expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        payload = [
            {
                "id": "channel.future",
                "name": "Future",
                "stream_url": "https://example.test/future.m3u8",
                "group": "web",
                "status": "resolved",
                "expires_at": future_expires_at,
                "ttl_seconds": 1,
            },
            {
                "id": "channel.past",
                "name": "Past",
                "stream_url": "https://example.test/past.m3u8",
                "group": "web",
                "status": "resolved",
                "expires_at": past_expires_at,
                "ttl_seconds": 3600,
            },
        ]
        client = FakeClient(payload)

        channels = fetch_channels(client, min_live_ttl=900)

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].id, "channel.future")
        self.assertGreater(channels[0].ttl_seconds, 900)
        self.assertLessEqual(channels[0].ttl_seconds, 3600)

    def test_preserves_extended_channel_transport_contract(self):
        payload = [
            {
                "id": "channel.4k",
                "name": "Channel [4K] [HEVC]",
                "stream_url": "https://example.test/channel.mpd",
                "group": "TV Aberta",
                "source_type": "stremio_addon",
                "provider_id": "addon_catalog_1",
                "logical_channel_id": "logical.channel",
                "variant_id": "variant.4k",
                "variant_label": "4K HEVC",
                "resolution": "2160p",
                "codec": "hevc",
                "protocol": "dash",
                "request_headers": {"Referer": "https://player.example.test/"},
                "publishable_static": True,
                "status": "resolved",
            }
        ]

        channels = fetch_channels(FakeClient(payload), min_live_ttl=900)

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].provider_id, "addon_catalog_1")
        self.assertEqual(channels[0].logical_channel_id, "logical.channel")
        self.assertEqual(channels[0].variant_label, "4K HEVC")
        self.assertEqual(channels[0].protocol, "dash")
        self.assertEqual(channels[0].raw_group, "TV Aberta")

    def test_ignores_removed_catalog_entries(self):
        payload = [
            {
                "id": "removed.channel",
                "name": "Removed",
                "stream_url": "https://example.test/removed.m3u8",
                "group": "web",
                "status": "removed",
                "removed": True,
            }
        ]

        self.assertEqual(fetch_channels(FakeClient(payload), min_live_ttl=900), [])


if __name__ == "__main__":
    unittest.main()
