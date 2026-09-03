import unittest

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel


class M3UExporterTest(unittest.TestCase):
    def test_renders_playlist_header_and_channels(self):
        channel = Channel(
            id="example.channel",
            name="Example Channel",
            stream_url="https://example.test/live.m3u8",
            logo="https://example.test/logo.png",
            group="Web Live",
            source="live_stream_catalog",
        )

        playlist = render_m3u([channel])

        self.assertTrue(playlist.startswith("#EXTM3U"))
        self.assertIn('x-tvg-url="https://bit.ly/legal-epg,', playlist)
        self.assertIn('group-title="Web Live"', playlist)
        self.assertIn('tvg-id=""', playlist)
        self.assertIn("https://example.test/live.m3u8", playlist)
        self.assertTrue(playlist.endswith("\n"))

    def test_uses_tvg_id_when_available(self):
        channel = Channel(
            id="internal.channel",
            name="Example Channel",
            stream_url="https://example.test/live.m3u8",
            logo="",
            group="Web Live",
            source="live_stream_catalog",
            tvg_id="ExampleChannel.br",
        )

        playlist = render_m3u([channel])

        self.assertIn('tvg-id="ExampleChannel.br"', playlist)
        self.assertNotIn('tvg-id="internal.channel"', playlist)

    def test_sanitizes_attribute_values(self):
        channel = Channel(
            id='example."channel"',
            name='Example "Quoted"\nChannel',
            stream_url="https://example.test/live.m3u8",
            logo='https://example.test/"logo".png',
            group="Web Live",
            source="live_stream_catalog",
        )

        playlist = render_m3u([channel])

        self.assertIn('group-title="Web Live"', playlist)
        self.assertIn('tvg-id=""', playlist)
        self.assertIn('tvg-name="Example \'Quoted\' Channel"', playlist)
        self.assertIn('tvg-logo="https://example.test/\'logo\'.png"', playlist)
        self.assertIn("Example \"Quoted\" Channel", playlist)
        self.assertNotIn("\nChannel", playlist)

    def test_full_portable_profile_uses_configured_guide_and_headers(self):
        channel = Channel(
            id="clear.dash",
            name="Clear DASH",
            stream_url="https://example.test/manifest.mpd",
            logo="",
            group="TV Aberta",
            source="live_stream_catalog",
            protocol="dash",
            request_headers={"Referer": "https://player.example.test/"},
        )

        playlist = render_m3u(
            [channel],
            guide_url="https://guide.example.test/guide.xml",
            category_order=("TV Aberta",),
            legacy_guide_urls=False,
            enforce_capabilities=True,
        )

        self.assertIn(
            'x-tvg-url="https://bit.ly/legal-epg,https://guide.example.test/guide.xml"',
            playlist,
        )
        self.assertIn("manifest.mpd|Referer=https%3A%2F%2Fplayer.example.test%2F", playlist)
        self.assertNotIn("#KODIPROP", playlist)

    def test_portable_profile_omits_drm_dynamic_and_ssai(self):
        base = dict(
            logo="",
            group="TV Aberta",
            source="live_stream_catalog",
        )
        channels = [
            Channel(
                id="drm",
                name="DRM",
                stream_url="https://example.test/drm.mpd",
                drm={"type": "widevine", "license_url": "https://license.example.test/"},
                **base,
            ),
            Channel(
                id="dynamic",
                name="Dynamic",
                stream_url="https://example.test/dynamic.m3u8",
                requires_dynamic_resolution=True,
                **base,
            ),
            Channel(
                id="ssai",
                name="SSAI",
                stream_url="https://example.test/ssai.m3u8",
                delivery_mode="ssai",
                **base,
            ),
        ]

        playlist = render_m3u(
            channels,
            category_order=("TV Aberta",),
            legacy_guide_urls=False,
            enforce_capabilities=True,
        )

        self.assertNotIn("example.test", playlist)

    def test_kodi_profile_renders_safe_widevine_properties(self):
        channel = Channel(
            id="drm",
            name="DRM",
            stream_url="https://example.test/drm.mpd",
            logo="",
            group="TV Aberta",
            source="live_stream_catalog",
            protocol="dash",
            drm={
                "type": "widevine",
                "license_url": "https://license.example.test/widevine",
                "license_headers": {"Origin": "https://player.example.test"},
            },
        )

        playlist = render_m3u(
            [channel],
            player_profile="kodi",
            category_order=("TV Aberta",),
            legacy_guide_urls=False,
            enforce_capabilities=True,
        )

        self.assertIn("inputstream.adaptive.manifest_type=mpd", playlist)
        self.assertIn("license_type=com.widevine.alpha", playlist)
        self.assertIn("license.example.test/widevine", playlist)

    def test_full_profile_never_serializes_sensitive_headers_or_signed_urls(self):
        channels = [
            Channel(
                id="header-secret",
                name="Header Secret",
                stream_url="https://example.test/header.m3u8",
                logo="",
                group="TV Aberta",
                source="live_stream_catalog",
                request_headers={"Authorization": "Bearer private"},
            ),
            Channel(
                id="signed-url",
                name="Signed URL",
                stream_url="https://example.test/signed.m3u8?token=private",
                logo="",
                group="TV Aberta",
                source="live_stream_catalog",
            ),
            Channel(
                id="empty-token",
                name="Empty Token",
                stream_url="https://blank.example.test/live.m3u8?token=",
                logo="",
                group="TV Aberta",
                source="iptv_org",
            ),
        ]

        playlist = render_m3u(
            channels,
            category_order=("TV Aberta",),
            legacy_guide_urls=False,
            enforce_capabilities=True,
        )

        self.assertNotIn("private", playlist)
        self.assertNotIn("example.test", playlist)

    def test_places_configured_alternatives_group_last(self):
        channels = [
            Channel(
                id="alternative",
                name="Canal [HD]",
                stream_url="https://alternative.test/live.m3u8",
                logo="",
                group="Opções",
                source="live_stream_catalog",
            ),
            Channel(
                id="primary",
                name="Canal [4K]",
                stream_url="https://primary.test/live.m3u8",
                logo="",
                group="TV Aberta",
                source="live_stream_catalog",
            ),
        ]

        playlist = render_m3u(
            channels,
            category_order=("Opções", "TV Aberta"),
            alternatives_group="Opções",
        )

        self.assertLess(
            playlist.index("### Canais TV Aberta"),
            playlist.index("### Canais Opções"),
        )


if __name__ == "__main__":
    unittest.main()
