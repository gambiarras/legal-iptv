import unittest

from legal_iptv.models import Channel
from legal_iptv.services.channel_selector import select_best_channels


def make_channel(
    *,
    id: str = "same.id",
    source: str = "live_stream_catalog",
    stream_url: str = "https://example.test/live.m3u8",
    status: str | None = "resolved",
    ttl_seconds: int | None = None,
    logo: str = "",
) -> Channel:
    return Channel(
        id=id,
        name=f"{source} channel",
        stream_url=stream_url,
        logo=logo,
        group="Web Live",
        source=source,
        status=status,
        ttl_seconds=ttl_seconds,
    )


class ChannelSelectorTest(unittest.TestCase):
    def test_selects_higher_priority_source_for_same_id(self):
        iptv_org = make_channel(source="iptv_org", status=None)
        live_catalog = make_channel(source="live_stream_catalog")

        selected = select_best_channels([iptv_org, live_catalog])

        self.assertEqual(selected, [live_catalog])

    def test_null_ttl_does_not_exclude_channel(self):
        null_ttl = make_channel(ttl_seconds=None)

        selected = select_best_channels([null_ttl])

        self.assertEqual(selected, [null_ttl])

    def test_prefers_positive_ttl_over_null_ttl_when_other_scores_match(self):
        null_ttl = make_channel(ttl_seconds=None)
        positive_ttl = make_channel(ttl_seconds=1200)

        selected = select_best_channels([null_ttl, positive_ttl])

        self.assertEqual(selected, [positive_ttl])

    def test_prefers_available_stream_when_other_scores_match(self):
        unavailable = make_channel(stream_url="")
        available = make_channel(stream_url="https://example.test/live.m3u8")

        selected = select_best_channels([unavailable, available])

        self.assertEqual(selected, [available])

    def test_prefers_logo_when_other_scores_match(self):
        without_logo = make_channel()
        with_logo = make_channel(logo="https://example.test/logo.png")

        selected = select_best_channels([without_logo, with_logo])

        self.assertEqual(selected, [with_logo])


if __name__ == "__main__":
    unittest.main()
