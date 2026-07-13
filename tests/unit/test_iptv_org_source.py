import unittest

from legal_iptv.sources.iptv_org import fetch_channels


class FakeClient:
    def get_json(self, url: str):
        if url.endswith("/channels.json"):
            return [
                {
                    "id": "SBT.br",
                    "name": "SBT",
                    "country": "BR",
                    "is_nsfw": False,
                    "categories": ["general"],
                }
            ]

        if url.endswith("/feeds.json"):
            return [
                {
                    "channel": "SBT.br",
                    "id": "Rio",
                    "name": "Rio de Janeiro",
                    "is_main": False,
                    "broadcast_area": ["s/BR-RJ"],
                }
            ]

        if url.endswith("/streams.json"):
            return [
                {
                    "channel": "SBT.br",
                    "feed": "Rio",
                    "url": "https://example.test/sbt.m3u8",
                }
            ]

        if url.endswith("/logos.json"):
            return [
                {
                    "channel": "SBT.br",
                    "feed": "Rio",
                    "url": "https://example.test/sbt.png",
                }
            ]

        if url.endswith("/subdivisions.json"):
            return [
                {
                    "country": "BR",
                    "name": "Rio de Janeiro",
                    "code": "BR-RJ",
                }
            ]

        if url.endswith("/cities.json"):
            return []

        raise AssertionError(f"Unexpected URL: {url}")


class IPTVOrgSourceTest(unittest.TestCase):
    def test_uses_channel_id_as_tvg_id(self):
        channels = fetch_channels(FakeClient())

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].id, "SBT.br")
        self.assertEqual(channels[0].name, "SBT RJ")
        self.assertEqual(channels[0].tvg_id, "SBT.br")
        self.assertEqual(channels[0].feed_id, "Rio")
        self.assertEqual(channels[0].stream_url, "https://example.test/sbt.m3u8")


if __name__ == "__main__":
    unittest.main()
