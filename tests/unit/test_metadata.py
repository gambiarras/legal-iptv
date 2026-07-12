import unittest

from legal_iptv.models import Channel
from legal_iptv.services.metadata import build_run_metadata


def make_channel(source: str, id: str) -> Channel:
    return Channel(
        id=id,
        name=id,
        stream_url=f"https://example.test/{id}.m3u8",
        logo="",
        group="Web Live",
        source=source,
    )


class MetadataTest(unittest.TestCase):
    def test_builds_rich_run_metadata(self):
        all_channels = [
            make_channel("extra", "extra"),
            make_channel("iptv_org", "iptv"),
            make_channel("live_stream_catalog", "live"),
        ]
        selected = [all_channels[2]]

        metadata = build_run_metadata(
            all_channels,
            selected,
            source_errors={"iptv_org": "boom"},
            selected_before_stream_filter=2,
        ).to_dict()

        self.assertEqual(metadata["total_input_channels"], 3)
        self.assertEqual(metadata["selected_channels"], 1)
        self.assertEqual(metadata["extra_channels"], 1)
        self.assertEqual(metadata["iptv_org_channels"], 1)
        self.assertEqual(metadata["live_stream_catalog_channels"], 1)
        self.assertEqual(metadata["source_errors"], {"iptv_org": "boom"})
        self.assertEqual(metadata["selected_before_stream_filter"], 2)
        self.assertEqual(metadata["stream_filtered_channels"], 1)
        self.assertEqual(metadata["deduplicated_channels"], 1)
        self.assertEqual(metadata["selected_by_source"], {"live_stream_catalog": 1})


if __name__ == "__main__":
    unittest.main()
