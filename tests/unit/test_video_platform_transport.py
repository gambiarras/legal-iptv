import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel
from legal_iptv.services.companion import select_companion_channels
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.services.publication import is_exportable
from legal_iptv.services.video_platform_transport import is_public_video_platform_hls


NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
EXPIRY = NOW + timedelta(hours=4)
STAMP = int(EXPIRY.timestamp())


def channel(**values):
    fields = dict(
        id="official.live", name="Official Live - Daily live", logo="", group="Notícias",
        source="live_stream_catalog", source_type="video_platform", provider_id="video_platform",
        source_url="https://www.video-platform.example.invalid/watch?v=abcdefghijk", status="resolved",
        stream_url=f"https://manifest.media-video.example.invalid/api/manifest/hls_variant/expire/{STAMP}/sig/test/index.m3u8",
        expires_at=EXPIRY.isoformat(), resolved_at=NOW.isoformat(), protocol="hls",
        publishable_static=False, requires_dynamic_resolution=True,
    )
    return Channel(**{**fields, **values})


class VideoPlatformTransportTest(unittest.TestCase):
    def setUp(self):
        clock = patch("legal_iptv.services.video_platform_transport.datetime")
        self.clock = clock.start()
        self.clock.now.return_value = NOW
        self.clock.fromtimestamp.side_effect = datetime.fromtimestamp
        self.clock.fromisoformat.side_effect = datetime.fromisoformat
        self.addCleanup(clock.stop)

    def test_public_media_signature_is_eligible_with_truthful_dynamic_flags(self):
        for url in (
            channel().stream_url,
            f"https://rr1.media-video.example.invalid/index.m3u8?expire={STAMP}&signature=public-media-signature",
            f"https://manifest.media-video.example.invalid/api/manifest/hls_playlist/expire/{STAMP}/sig/test",
        ):
            with self.subTest(url=url):
                candidate = channel(stream_url=url)
                self.assertTrue(is_public_video_platform_hls(candidate, now=NOW))
                self.assertTrue(is_exportable(candidate, "portable"))
                self.assertFalse(candidate.publishable_static)
                self.assertTrue(candidate.requires_dynamic_resolution)

    def test_rejects_private_unresolved_untrusted_and_expired_transports(self):
        cases = [
            {"source_type": "addon_catalog"}, {"status": "error"}, {"removed": True},
            {"drm": {"type": "widevine"}}, {"secret_refs": {"headers": "PRIVATE_HEADERS"}},
            {"delivery_mode": "ssai"}, {"request_headers": {"Cookie": "private"}},
            {"request_headers": {"Authorization": "private"}},
            {"stream_url": "https://www.video-platform.example.invalid/watch?v=abcdefghijk"},
            {"stream_url": f"https://evilmedia-video.example.invalid/index.m3u8?expire={STAMP}"},
            {"stream_url": f"https://media-video.example.invalid.evil.test/index.m3u8?expire={STAMP}"},
            {"stream_url": f"http://rr1.media-video.example.invalid/index.m3u8?expire={STAMP}"},
            {"stream_url": f"https://user:password@rr1.media-video.example.invalid/index.m3u8?expire={STAMP}"},
            {"stream_url": f"https://rr1.media-video.example.invalid:444/index.m3u8?expire={STAMP}"},
            {"stream_url": f"https://rr1.media-video.example.invalid/videoplayback?expire={STAMP}"},
            {"stream_url": "https://rr1.media-video.example.invalid/index.m3u8"},
            {"stream_url": "https://rr1.media-video.example.invalid/index.m3u8?expire=1"},
            {"stream_url": f"https://rr1.media-video.example.invalid/index.m3u8?expire={STAMP}&token="},
            {"stream_url": f"https://rr1.media-video.example.invalid/index.m3u8?expire={STAMP}&username=private"},
            {"stream_url": f"https://rr1.media-video.example.invalid/token/private/expire/{STAMP}/index.m3u8"},
            {"stream_url": "https://[invalid/index.m3u8"},
            {"expires_at": (NOW - timedelta(seconds=1)).isoformat()},
            {"expires_at": "invalid"}, {"expires_at": "2026-11-21T16:00:00"},
            {"source_url": "https://video-platform.example.invalid.evil.test/watch?v=abcdefghijk"},
            {"source_url": "https://video-platform.example.invalid/watch?v=abcdefghijk&access_token=private"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertFalse(is_public_video_platform_hls(channel(**changes), now=NOW))

    def test_expiry_uses_shortest_declaration_and_requires_sufficient_remaining_life(self):
        candidate = channel()
        self.assertTrue(is_public_video_platform_hls(candidate, now=EXPIRY - timedelta(seconds=900)))
        self.assertFalse(is_public_video_platform_hls(candidate, now=EXPIRY - timedelta(seconds=899)))
        self.assertFalse(is_public_video_platform_hls(replace(candidate,
            stream_url=candidate.stream_url + "?expire=1"), now=NOW))
        self.assertFalse(is_public_video_platform_hls(replace(candidate,
            stream_url=candidate.stream_url + "?expire=invalid"), now=NOW))

    def test_missing_private_host_configuration_and_unrecognized_static_claims_fail_closed(self):
        with patch("legal_iptv.services.video_platform_transport.runtime_value", return_value={}):
            self.assertFalse(is_public_video_platform_hls(channel(), now=NOW))
        candidate = channel(stream_url="https://untrusted.example.invalid/index.m3u8",
                            publishable_static=True, requires_dynamic_resolution=False)
        self.assertFalse(is_exportable(candidate, "portable"))

    def test_public_source_shapes_and_remaining_ttl_are_checked_independently(self):
        sources = (
            "https://short-video.example.invalid/abcdefghijk",
            "https://video-platform.example.invalid/live/abcdefghijk",
            "https://video-platform.example.invalid/@official/live",
            "https://video-platform.example.invalid/channel/example/live",
        )
        for source in sources:
            self.assertTrue(is_public_video_platform_hls(channel(source_url=source), now=NOW))
        self.assertTrue(is_public_video_platform_hls(channel(ttl_seconds=900), now=NOW))
        for remaining in (0, 899, 900.5, True, "900", float("nan"), float("inf")):
            with self.subTest(remaining=remaining):
                self.assertFalse(is_public_video_platform_hls(channel(ttl_seconds=remaining), now=NOW))
        for source in ("https://video-platform.example.invalid/", sources[0] + "?signature=value"):
            self.assertFalse(is_public_video_platform_hls(channel(source_url=source), now=NOW))
        self.assertFalse(is_public_video_platform_hls(channel(request_headers={" Cookie ": "private"}), now=NOW))

    def test_full_and_companion_publish_same_eligible_hls_without_plugin_urls(self):
        candidate = channel()
        configuration = load_playlist_configuration()
        selected = select_profile_channels([candidate], configuration, "full")
        self.assertEqual(len(selected), 1)
        companion, excluded = select_companion_channels(
            selected, {"base_reference_channels": []}, preselected=True)
        self.assertEqual(excluded, [])
        self.assertEqual(companion, selected)
        for channels in (selected, companion):
            playlist = render_m3u(channels, category_order=configuration.group_order,
                                  enforce_capabilities=True)
            self.assertEqual(playlist.count("#EXTINF:"), 1)
            self.assertIn(candidate.stream_url, playlist)
            self.assertNotIn("plugin://", playlist)

    def test_non_video_platform_private_or_dynamic_transport_remains_excluded(self):
        for candidate in (channel(source_type="addon_catalog"),
                          channel(source_type="video_platform", request_headers={"Cookie": "secret"}),
                          channel(stream_url="https://media.example/live.m3u8?token=secret")):
            self.assertFalse(is_exportable(candidate, "portable"))
            self.assertEqual(select_profile_channels(
                [candidate], load_playlist_configuration(), "full"), [])


if __name__ == "__main__":
    unittest.main()
