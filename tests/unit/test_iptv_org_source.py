import json
import tempfile
import unittest
from pathlib import Path

from legal_iptv.sources.iptv_org import API_FILES, fetch_channels


def api_payload() -> dict[str, list[dict]]:
    return {
        "channels": [
            {
                "id": "SBT.br",
                "name": "SBT",
                "country": "BR",
                "is_nsfw": False,
                "categories": ["general"],
            }
        ],
        "feeds": [
            {
                "channel": "SBT.br",
                "id": "Rio",
                "name": "Rio de Janeiro",
                "is_main": False,
                "broadcast_area": ["s/BR-RJ"],
            }
        ],
        "streams": [
            {
                "channel": "SBT.br",
                "feed": "Rio",
                "url": "https://example.test/sbt.m3u8",
            }
        ],
        "logos": [
            {
                "channel": "SBT.br",
                "feed": "Rio",
                "url": "https://example.test/sbt.png",
            }
        ],
        "subdivisions": [
            {
                "country": "BR",
                "name": "Rio de Janeiro",
                "code": "BR-RJ",
            }
        ],
        "cities": [],
    }


class FakeClient:
    def __init__(self, payload: dict[str, list[dict]] | None = None):
        self.payload = payload or api_payload()
        self.calls: list[str] = []

    def get_json(self, url: str):
        self.calls.append(url)
        for name in API_FILES:
            if url.endswith(f"/{name}.json"):
                return self.payload[name]

        raise AssertionError(f"Unexpected URL: {url}")


class FailingClient:
    def get_json(self, url: str):
        raise RuntimeError("network failed")


def cached_channel() -> dict:
    return {
        "id": "SBT.br",
        "name": "SBT RJ",
        "stream_url": "https://example.test/sbt.m3u8",
        "logo": "https://example.test/sbt.png",
        "group": "Variedades",
        "source": "iptv_org",
        "tvg_id": "SBT.br",
        "feed_id": "Rio",
    }


def write_cache(cache_file: Path, generated_at: str) -> None:
    cache_file.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "channels": [cached_channel()],
            }
        ),
        encoding="utf-8",
    )


class IPTVOrgSourceTest(unittest.TestCase):
    def test_uses_channel_id_as_tvg_id(self):
        channels = fetch_channels(FakeClient())

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].id, "SBT.br")
        self.assertEqual(channels[0].name, "SBT RJ")
        self.assertEqual(channels[0].tvg_id, "SBT.br")
        self.assertEqual(channels[0].feed_id, "Rio")
        self.assertEqual(channels[0].stream_url, "https://example.test/sbt.m3u8")

    def test_uses_fresh_cache_without_fetching_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "iptv-org-cache.json"
            write_cache(cache_file, "2999-01-01T00:00:00+00:00")
            client = FailingClient()

            channels = fetch_channels(client, cache_file=cache_file)

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].name, "SBT RJ")

    def test_writes_cache_after_fetching_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "iptv-org-cache.json"
            client = FakeClient()

            channels = fetch_channels(client, cache_file=cache_file)
            payload = json.loads(cache_file.read_text(encoding="utf-8"))

        self.assertEqual(len(channels), 1)
        self.assertEqual(len(client.calls), len(API_FILES))
        self.assertEqual(payload["channels"][0]["id"], "SBT.br")
        self.assertNotIn("data", payload)

    def test_uses_stale_cache_when_api_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "iptv-org-cache.json"
            write_cache(cache_file, "2000-01-01T00:00:00+00:00")

            channels = fetch_channels(
                FailingClient(),
                cache_file=cache_file,
                cache_ttl_seconds=1,
            )

        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0].stream_url, "https://example.test/sbt.m3u8")


if __name__ == "__main__":
    unittest.main()
