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


def channel(name="Required Channel", provider="extra", **values):
    return Channel(id=provider, name=name, stream_url=f"https://{provider}.example/live.m3u8",
                   logo="", group="Infantil", source=provider if provider in {"extra", "public_catalog"}
                   else "live_stream_catalog", provider_id=provider, **values)


class PlaylistPolicyTest(unittest.TestCase):
    def test_source_order_precedes_resolution_and_lower_sources_only_fill_missing_channels(self):
        providers = ("extra", "video_platform", "public_catalog", "rest_catalog_1", "directory_catalog")
        remaining = [channel(provider=provider, resolution="2160p" if provider == "extra" else "720p")
                     for provider in providers]
        configuration = load_playlist_configuration()
        for winner in reversed(providers):
            selected = select_profile_channels(remaining, configuration, "full")
            self.assertEqual([item.provider_id for item in selected], [winner])
            remaining = [item for item in remaining if item.provider_id != winner]

    def test_alias_station_a_has_one_shared_id_and_keeps_the_first_provider(self):
        selected = select_profile_channels([
            channel("Example Station A", "extra"), channel("Example Station A", "rest_catalog_1"),
        ], load_playlist_configuration(), "full")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].provider_id, "rest_catalog_1")
        self.assertEqual(selected[0].tvg_id, "ExampleChannel054.br")

    def test_series_classic_aliases_do_not_create_duplicate_channels_or_unstable_ids(self):
        configuration = load_playlist_configuration()
        candidates = [channel("Series Example", "extra", tvg_id="ExampleChannel012.br"),
                      channel("Series Example", "public_catalog", tvg_id="ExampleChannel012.br"),
                      channel("Series Example", "rest_catalog_1", tvg_id="ExampleChannel012.br")]
        selected = select_profile_channels(candidates, configuration, "full")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].tvg_id, "ExampleChannel012.br")
        self.assertEqual(selected[0].provider_id, "rest_catalog_1")
        self.assertEqual(select_profile_channels(selected, configuration, "full"), selected)

    def test_shared_id_and_url_do_not_hide_a_higher_provider_with_different_headers(self):
        lower = channel("Public Channel", "extra", request_headers={"Referer": "https://lower.example/"})
        higher = replace(lower, source="public_catalog", provider_id="public_catalog",
                         request_headers={"Referer": "https://higher.example/"})
        winner = select_profile_channels([lower, higher], load_playlist_configuration(), "full")[0]
        self.assertEqual(winner.provider_id, "public_catalog")
        self.assertEqual(winner.request_headers, higher.request_headers)

    def test_regional_feeds_do_not_share_an_ambiguous_guide_id(self):
        values = canonicalize_channels([
            channel("Example Channel 001", tvg_id="NetworkExample.br"),
            channel("NetworkExample RJ", "public_catalog", tvg_id="NetworkExample.br"),
        ])
        self.assertEqual(len({item.logical_channel_id for item in values}), 2)
        self.assertEqual(len({item.tvg_id for item in values}), 2)
        self.assertNotIn("NetworkExample.br", {item.tvg_id for item in values})

    def test_tv_alternativa_is_a_broadcaster_not_an_option_label(self):
        self.assertIsNone(rejection_reason(channel("TV Alternativa")))
        for name in ("TV Pública Alternativo", "TV Pública Alternativo 2 [HD]", "TV Pública Backup"):
            self.assertEqual(rejection_reason(channel(name)), "alternative")

    def test_paid_rest_catalog_1_and_excluded_provider_are_excluded(self):
        self.assertEqual(rejection_reason(channel("PremiumChannel", "rest_catalog_1")), "paid_channel")
        self.assertEqual(rejection_reason(channel("Required Channel", "addon_catalog_1")), "excluded_provider")
        self.assertEqual(rejection_reason(channel("Required Channel", "json_catalog_1")), "excluded_provider")

    def test_companion_is_a_subset_with_identical_transport_and_metadata(self):
        winners = select_profile_channels([
            channel(), channel("Example Station A", "rest_catalog_1"), channel("Sports Example", "directory_catalog"),
        ], load_playlist_configuration(), "full")
        selected, _ = select_companion_channels(
            winners, {"base_reference_channels": ["Sports Example"], "directory_catalog_channels": []}, preselected=True)
        self.assertEqual({item.name for item in selected}, {"Required Channel", "Example Station A"})
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
