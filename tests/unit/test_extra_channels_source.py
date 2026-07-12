import unittest

from legal_iptv.sources.extra_channels import fetch_channels


class ExtraChannelsSourceTest(unittest.TestCase):
    def test_loads_packaged_extra_channels_resource(self):
        channels = fetch_channels()

        self.assertGreater(len(channels), 0)
        self.assertTrue(all(channel.source == "extra" for channel in channels))
        self.assertTrue(all(channel.stream_url for channel in channels))


if __name__ == "__main__":
    unittest.main()
