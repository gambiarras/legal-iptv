import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel
from legal_iptv.services.channel_selector import select_best_channels
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.services.link_validator import filter_cached_offline_channels


def channel(
    id: str,
    name: str,
    url: str,
    *,
    provider: str,
    source: str = "live_stream_catalog",
    source_type: str | None = None,
    tvg_id: str | None = None,
    variant: str | None = None,
    raw_group: str = "general",
    **values,
) -> Channel:
    return Channel(
        id=id,
        name=name,
        stream_url=url,
        logo="",
        group="Variedades",
        source=source,
        source_type=source_type or provider,
        provider_id=provider,
        logical_channel_id="logical.channel",
        variant_id=variant or id,
        tvg_id=tvg_id,
        raw_group=raw_group,
        status="resolved",
        **values,
    )


class ProfileSelectorTest(unittest.TestCase):
    def setUp(self):
        self.configuration = load_playlist_configuration()

    def test_base_profile_matches_legacy_selection_and_rendering(self):
        channels = [
            channel(
                "same",
                "Example",
                "https://example.test/live.m3u8",
                provider="extra",
                source="extra",
                source_type=None,
            ),
            channel(
                "same",
                "Example",
                "https://example.test/live.m3u8",
                provider="iptv_org",
                source="iptv_org",
                source_type=None,
            ),
            channel(
                "new",
                "New provider",
                "https://example.test/new.m3u8",
                provider="addon_catalog_1",
                source_type="stremio_addon",
            ),
        ]

        legacy = render_m3u(select_best_channels(channels[:2]))
        selected = select_profile_channels(channels, self.configuration, "base")

        self.assertEqual(render_m3u(selected), legacy)
        self.assertNotIn("new.m3u8", legacy)

    def test_full_keeps_all_variants_from_winning_provider(self):
        channels = [
            channel(
                "high.fhd",
                "Canal [FHD]",
                "https://high.example/fhd.m3u8",
                provider="addon_catalog_1",
                tvg_id="Canal.br",
                variant="fhd",
            ),
            channel(
                "high.4k",
                "Canal [4K]",
                "https://high.example/4k.mpd",
                provider="addon_catalog_1",
                tvg_id="Canal.br",
                variant="4k",
            ),
            channel(
                "low",
                "Canal",
                "https://low.example/live.m3u8",
                provider="iptv_org",
                source="iptv_org",
                tvg_id="Canal.br",
            ),
        ]

        selected = select_profile_channels(channels, self.configuration, "full")

        self.assertEqual({item.id for item in selected}, {"high.fhd", "high.4k"})
        self.assertEqual({item.tvg_id for item in selected}, {"Canal.br"})

    def test_full_uses_explicit_logical_id_across_different_names(self):
        high = channel(
            "high",
            "Canal Região [FHD]",
            "https://high.example/live.m3u8",
            provider="addon_catalog_1",
        )
        low = channel(
            "low",
            "Canal Nacional",
            "https://low.example/live.m3u8",
            provider="iptv_org",
            source="iptv_org",
        )

        selected = select_profile_channels([low, high], self.configuration, "full")

        self.assertEqual([item.id for item in selected], ["high"])

    def test_full_treats_different_url_queries_as_distinct_variants(self):
        first = channel(
            "first",
            "Canal [HD]",
            "https://example.test/live.m3u8?variant=hd",
            provider="addon_catalog_1",
            variant="hd",
        )
        second = channel(
            "second",
            "Canal [FHD]",
            "https://example.test/live.m3u8?variant=fhd",
            provider="addon_catalog_1",
            variant="fhd",
        )

        selected = select_profile_channels([first, second], self.configuration, "full")

        self.assertEqual({item.id for item in selected}, {"first", "second"})

    def test_full_falls_back_when_higher_priority_provider_is_cached_offline(self):
        high = channel(
            "high",
            "Canal",
            "https://high.example/live.m3u8",
            provider="addon_catalog_1",
            tvg_id="Canal.br",
        )
        low = channel(
            "low",
            "Canal",
            "https://low.example/live.m3u8",
            provider="iptv_org",
            source="iptv_org",
            tvg_id="Canal.br",
        )
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "stream-status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "urls": {
                            high.stream_url: {
                                "active": False,
                                "checked_at": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            available = filter_cached_offline_channels(
                [high, low],
                status_file=status_path,
                max_age_seconds=14400,
            )

        selected = select_profile_channels(available, self.configuration, "full")

        self.assertEqual([item.id for item in selected], ["low"])

    def test_full_normalizes_variant_suffix_from_transport_metadata(self):
        candidate = channel(
            "channel.4k",
            "Canal",
            "https://example.test/channel.mpd",
            provider="addon_catalog_1",
            variant="4k",
            resolution="2160p",
            codec="h265",
        )

        selected = select_profile_channels([candidate], self.configuration, "full")

        self.assertEqual(selected[0].name, "Canal [4K] [HEVC]")

    def test_full_filters_only_explicit_adult_and_preserves_adult_swim(self):
        channels = [
            channel(
                "adult-swim",
                "Adult Swim",
                "https://example.test/adult-swim.m3u8",
                provider="addon_catalog_1",
                raw_group="animation",
            ),
            channel(
                "explicit",
                "Explicit +18",
                "https://example.test/explicit.m3u8",
                provider="addon_catalog_1",
                raw_group="entertainment",
            ),
        ]

        selected = select_profile_channels(channels, self.configuration, "full")

        self.assertEqual([item.id for item in selected], ["adult-swim"])
        self.assertEqual(selected[0].group, "Animação")

    def test_full_rejects_dynamic_secret_and_dai_channels(self):
        candidates = [
            channel(
                "dynamic",
                "Dynamic",
                "https://example.test/dynamic.m3u8",
                provider="addon_catalog_1",
                requires_dynamic_resolution=True,
                publishable_static=False,
            ),
            channel(
                "secret",
                "Secret",
                "https://example.test/secret.m3u8",
                provider="addon_catalog_1",
                secret_refs={"headers": "PRIVATE_HEADERS"},
            ),
            channel(
                "dai",
                "DAI",
                "https://example.test/dai.m3u8",
                provider="addon_catalog_1",
                delivery_mode="dai",
            ),
        ]

        self.assertEqual(
            select_profile_channels(candidates, self.configuration, "full"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
