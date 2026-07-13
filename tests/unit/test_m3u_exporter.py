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


if __name__ == "__main__":
    unittest.main()
