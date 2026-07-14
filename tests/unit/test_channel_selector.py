import unittest

from legal_iptv.models import Channel
from legal_iptv.services.channel_selector import select_best_channels


def make_channel(
    *,
    id: str = "same.id",
    name: str | None = None,
    source: str = "live_stream_catalog",
    stream_url: str = "https://example.test/live.m3u8",
    status: str | None = "resolved",
    ttl_seconds: int | None = None,
    logo: str = "",
    tvg_id: str | None = None,
) -> Channel:
    return Channel(
        id=id,
        name=name or f"{source} channel",
        stream_url=stream_url,
        logo=logo,
        group="Web Live",
        source=source,
        status=status,
        ttl_seconds=ttl_seconds,
        tvg_id=tvg_id,
    )


class ChannelSelectorTest(unittest.TestCase):
    def test_selects_live_catalog_for_duplicate_same_url(self):
        iptv_org = make_channel(
            source="iptv_org",
            status=None,
            stream_url="https://example.test/live.m3u8",
        )
        live_catalog = make_channel(
            source="live_stream_catalog",
            stream_url="https://example.test/live.m3u8",
        )

        selected = select_best_channels([iptv_org, live_catalog])

        self.assertEqual(selected, [live_catalog])

    def test_keeps_same_id_when_url_is_different_with_unique_ids(self):
        extra = make_channel(
            source="extra",
            stream_url="https://example.test/extra.m3u8",
        )
        live_catalog = make_channel(
            source="live_stream_catalog",
            stream_url="https://example.test/live.m3u8",
        )

        selected = select_best_channels([extra, live_catalog])

        self.assertEqual(len(selected), 2)
        self.assertEqual(
            {channel.stream_url for channel in selected},
            {
                "https://example.test/extra.m3u8",
                "https://example.test/live.m3u8",
            },
        )
        self.assertEqual(len({channel.id for channel in selected}), 2)

    def test_skips_similar_name_with_same_url(self):
        extra = make_channel(
            id="extra.channel",
            name="TV Cultura",
            source="extra",
            stream_url="https://example.test/cultura.m3u8",
        )
        live_catalog = make_channel(
            id="live.channel",
            name="Tv Cúltura",
            source="live_stream_catalog",
            stream_url="https://example.test/cultura.m3u8",
        )

        selected = select_best_channels([extra, live_catalog])

        self.assertEqual(selected, [live_catalog])

    def test_skips_different_name_with_same_url(self):
        extra = make_channel(
            id="Loading.br",
            name="Loading",
            source="extra",
            stream_url="https://stmv1.srvif.com/loadingtv/loadingtv/playlist.m3u8",
            tvg_id="Loading.br",
        )
        iptv_org = make_channel(
            id="TVLoading.br",
            name="TV Loading",
            source="iptv_org",
            stream_url="https://stmv1.srvif.com/loadingtv/loadingtv/playlist.m3u8",
            tvg_id="Loading.br",
        )

        selected = select_best_channels([iptv_org, extra])

        self.assertEqual(selected, [extra])

    def test_prefers_query_url_for_same_hls_playlist(self):
        plain = make_channel(
            id="fox.kids.plain",
            name="Fox Kids",
            source="extra",
            stream_url="https://stmv2.srvif.com/emlsilva/emlsilva/playlist.m3u8",
        )
        with_query = make_channel(
            id="fox.kids.query",
            name="Fox Kids",
            source="iptv_org",
            stream_url=(
                "https://stmv2.srvif.com/emlsilva/emlsilva/playlist.m3u8"
                "?hls_ctx=h517o66o"
            ),
        )

        selected = select_best_channels([plain, with_query])

        self.assertEqual(selected, [with_query])

    def test_prefers_playlist_over_hls_chunklist_variant(self):
        playlist = make_channel(
            id="retro.playlist",
            name="Retro Cartoon",
            source="extra",
            stream_url=(
                "https://stmv1.video.brstream.com.br/retrotv/retrotv/playlist.m3u8"
            ),
        )
        chunklist = make_channel(
            id="retro.chunklist",
            name="Retro Cartoon",
            source="live_stream_catalog",
            stream_url=(
                "https://stmv1.video.brstream.com.br/retrotv/retrotv/"
                "chunklist_w1857106462.m3u8"
            ),
        )

        selected = select_best_channels([playlist, chunklist])

        self.assertEqual(selected, [playlist])

    def test_keeps_similar_name_when_url_is_different(self):
        first = make_channel(
            id="first.channel",
            name="TV Cultura",
            stream_url="https://example.test/first.m3u8",
        )
        second = make_channel(
            id="second.channel",
            name="Tv Cúltura",
            stream_url="https://example.test/second.m3u8",
        )

        selected = select_best_channels([first, second])

        self.assertEqual(len(selected), 2)
        self.assertEqual(
            {channel.stream_url for channel in selected},
            {
                "https://example.test/first.m3u8",
                "https://example.test/second.m3u8",
            },
        )

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

    def test_keeps_same_tvg_id_when_url_is_different_and_marks_alternative(self):
        iptv_org = make_channel(
            id="Band.br",
            name="Band",
            source="iptv_org",
            stream_url="https://example.test/iptv.m3u8",
            tvg_id="Band.br",
        )
        live_catalog = make_channel(
            id="script_catalog_1.41",
            name="Band",
            source="live_stream_catalog",
            stream_url="https://example.test/live.m3u8",
            tvg_id="Band.br",
        )

        selected = select_best_channels([iptv_org, live_catalog])

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].name, "Band")
        self.assertEqual(selected[1].name, "Band Alternativo 1")

    def test_keeps_same_tvg_id_when_names_are_regional(self):
        sp = make_channel(
            id="sbt.sp",
            name="SBT SP",
            stream_url="https://example.test/sp.m3u8",
            tvg_id="SBT.br",
        )
        rio = make_channel(
            id="sbt.rio",
            name="SBT Rio",
            stream_url="https://example.test/rio.m3u8",
            tvg_id="SBT.br",
        )

        selected = select_best_channels([sp, rio])

        self.assertEqual(len(selected), 2)


if __name__ == "__main__":
    unittest.main()
