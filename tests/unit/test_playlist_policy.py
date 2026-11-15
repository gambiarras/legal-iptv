import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel
from legal_iptv.services.channel_identity import canonicalize_channels, rejection_reason
from legal_iptv.services.companion import select_companion_channels, write_guide_alias
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels


def channel(name="Fox Kids", provider="extra", **values):
    return Channel(id=provider, name=name, stream_url=f"https://{provider}.example/live.m3u8",
                   logo="", group="Infantil", source=provider if provider in {"extra", "iptv_org"}
                   else "live_stream_catalog", provider_id=provider, **values)


class PlaylistPolicyTest(unittest.TestCase):
    def test_source_order_precedes_resolution_and_lower_sources_only_fill_missing_channels(self):
        providers = ("extra", "youtube", "iptv_org", "rest_catalog_1", "televisao_tv")
        remaining = [channel(provider=provider, resolution="2160p" if provider == "extra" else "720p")
                     for provider in providers]
        configuration = load_playlist_configuration()
        for winner in reversed(providers):
            selected = select_profile_channels(remaining, configuration, "full")
            self.assertEqual([item.provider_id for item in selected], [winner])
            remaining = [item for item in remaining if item.provider_id != winner]

    def test_alias_sonata_has_one_shared_id_and_keeps_the_first_provider(self):
        selected = select_profile_channels([
            channel("TV Sonata", "extra"), channel("Sonata TV", "rest_catalog_1"),
        ], load_playlist_configuration(), "full")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].provider_id, "rest_catalog_1")
        self.assertEqual(selected[0].tvg_id, "TVSonata.br")

    def test_series_classic_aliases_do_not_create_duplicate_channels_or_unstable_ids(self):
        configuration = load_playlist_configuration()
        candidates = [channel("Canal Séries Classic", "extra", tvg_id="SeriesClassic.br"),
                      channel("Séries Classic", "iptv_org", tvg_id="SeriesClassic.br"),
                      channel("Series Classicas", "rest_catalog_1", tvg_id="SeriesClassic.br")]
        selected = select_profile_channels(candidates, configuration, "full")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].tvg_id, "SeriesClassic.br")
        self.assertEqual(selected[0].provider_id, "rest_catalog_1")
        self.assertEqual(select_profile_channels(selected, configuration, "full"), selected)

    def test_shared_id_and_url_do_not_hide_a_higher_provider_with_different_headers(self):
        lower = channel("Public Channel", "extra", request_headers={"Referer": "https://lower.example/"})
        higher = replace(lower, source="iptv_org", provider_id="iptv_org",
                         request_headers={"Referer": "https://higher.example/"})
        winner = select_profile_channels([lower, higher], load_playlist_configuration(), "full")[0]
        self.assertEqual(winner.provider_id, "iptv_org")
        self.assertEqual(winner.request_headers, higher.request_headers)

    def test_regional_feeds_do_not_share_an_ambiguous_guide_id(self):
        values = canonicalize_channels([
            channel("Globo SP", tvg_id="Globo.br"),
            channel("Globo RJ", "iptv_org", tvg_id="Globo.br"),
        ])
        self.assertEqual(len({item.logical_channel_id for item in values}), 2)
        self.assertEqual(len({item.tvg_id for item in values}), 2)
        self.assertNotIn("Globo.br", {item.tvg_id for item in values})

    def test_tv_alternativa_is_a_broadcaster_not_an_option_label(self):
        self.assertIsNone(rejection_reason(channel("TV Alternativa")))
        for name in ("TV Pública Alternativo", "TV Pública Alternativo 2 [HD]", "TV Pública Backup"):
            self.assertEqual(rejection_reason(channel(name)), "alternative")

    def test_paid_batista_and_frost_are_excluded(self):
        self.assertEqual(rejection_reason(channel("HBO", "rest_catalog_1")), "paid_channel")
        self.assertEqual(rejection_reason(channel("Fox Kids", "addon_catalog_1")), "excluded_provider")
        self.assertEqual(rejection_reason(channel("Fox Kids", "json_catalog_1")), "excluded_provider")

    def test_companion_is_a_subset_with_identical_transport_and_metadata(self):
        winners = select_profile_channels([
            channel(), channel("TV Sonata", "rest_catalog_1"), channel("CazéTV", "televisao_tv"),
        ], load_playlist_configuration(), "full")
        selected, _ = select_companion_channels(
            winners, {"saimo_channels": ["Cazé TV"], "televisao_channels": []}, preselected=True)
        self.assertEqual({item.name for item in selected}, {"Fox Kids", "TV Sonata"})
        self.assertTrue(all(any(item is candidate for candidate in winners) for item in selected))
        url = "https://our.example/guide.xml"
        self.assertEqual(render_m3u(winners, guide_url=url).splitlines()[0],
                         render_m3u(selected, guide_url=url).splitlines()[0])
        self.assertTrue(render_m3u(selected, guide_url=url).split('x-tvg-url="')[1].startswith(url + ","))

    def test_guide_compatibility_name_is_identical_and_does_not_accumulate_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "guide.xml", Path(directory) / "companion-guide.xml"
            source.write_bytes(b"<tv/>")
            for _ in range(3):
                write_guide_alias(source, target)
            self.assertEqual(source.read_bytes(), target.read_bytes())
            self.assertEqual(len(list(Path(directory).iterdir())), 2)
