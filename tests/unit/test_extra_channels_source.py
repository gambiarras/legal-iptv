import unittest

from legal_iptv.sources.extra_channels import fetch_channels


class ExtraChannelsSourceTest(unittest.TestCase):
    def test_loads_packaged_extra_channels_resource(self):
        channels = fetch_channels()
        channels_by_id = {channel.id: channel for channel in channels}

        self.assertGreater(len(channels), 0)
        self.assertTrue(all(channel.source == "extra" for channel in channels))
        self.assertTrue(all(channel.stream_url for channel in channels))
        self.assertEqual(channels_by_id["CNNBrasil.br"].tvg_id, "CNNBrasil.br")
        self.assertEqual(channels_by_id["MyTimemovienetworkBrazil.br"].tvg_id, "MyTimeMovieNetwork.br")
        self.assertEqual(channels_by_id["AniTV.br"].tvg_id, "Geekdot.br")
        self.assertIsNone(channels_by_id["RunTime.br"].tvg_id)


if __name__ == "__main__":
    unittest.main()
