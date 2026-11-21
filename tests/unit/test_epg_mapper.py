import gzip
import json
import tempfile
import unittest
from legal_iptv.local_config import resource_path
from pathlib import Path
from unittest.mock import patch

from legal_iptv.models import Channel
from legal_iptv.services.epg_mapper import (
    enrich_epg_metadata,
    load_xmltv_aliases,
    parse_xmltv_aliases,
)


def make_channel(
    *,
    id: str,
    name: str,
    source: str,
    tvg_id: str | None = None,
) -> Channel:
    return Channel(
        id=id,
        name=name,
        stream_url=f"https://example.test/{id}.m3u8",
        logo="",
        group="Web Live",
        source=source,
        tvg_id=tvg_id,
    )


class EPGMapperTest(unittest.TestCase):
    def test_every_configured_exact_alias_resolves_to_its_declared_tvg_id(self):
        resource = resource_path("epg_aliases.json")
        configured = json.loads(resource.read_text(encoding="utf-8"))

        for item_index, item in enumerate(configured):
            for alias_index, alias in enumerate(item.get("aliases", ())):
                with self.subTest(alias=alias):
                    channel = make_channel(
                        id=f"alias.{item_index}.{alias_index}",
                        name=alias,
                        source="live_stream_catalog",
                    )

                    enriched = enrich_epg_metadata([channel])

                    self.assertEqual(enriched[0].tvg_id, item["tvg_id"])

    def test_parses_xmltv_aliases_from_display_names(self):
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="ExampleChannel005.br">
    <display-name>BR - ExampleChannel005</display-name>
    <display-name>ExampleChannel005</display-name>
  </channel>
</tv>
"""

        aliases = parse_xmltv_aliases(payload)

        self.assertEqual(aliases["examplechannel005"].tvg_id, "ExampleChannel005.br")

    def test_parses_only_programmed_xmltv_channels_when_required(self):
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="ExampleChannel005.epg-pw">
    <display-name>ExampleChannel005</display-name>
  </channel>
  <channel id="ExampleChannel005.br">
    <display-name>ExampleChannel005</display-name>
  </channel>
  <programme channel="ExampleChannel005.br" start="20260714000000 +0000" stop="20260714010000 +0000">
    <title>Program</title>
  </programme>
</tv>
"""

        aliases = parse_xmltv_aliases(payload, require_programmes=True)

        self.assertEqual(aliases["examplechannel005"].tvg_id, "ExampleChannel005.br")

    def test_duplicate_name_prefers_first_programmed_channel(self):
        payload = b"""<tv>
  <channel id="primary"><display-name>PremiumExample Channel</display-name></channel>
  <channel id="fallback"><display-name>PremiumExample Channel</display-name></channel>
  <programme channel="primary" start="20260714000000 +0000" stop="20260714010000 +0000"/>
  <programme channel="fallback" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>"""

        aliases = parse_xmltv_aliases(payload, require_programmes=True)

        self.assertEqual(aliases["premiumexample channel"].tvg_id, "primary")

    def test_load_xmltv_aliases_prefers_first_source_with_programming(self):
        class FakeClient:
            def get_bytes(self, url: str) -> bytes:
                payloads = {
                    "https://example.test/epg-pw.xml.gz": b"""<tv>
  <channel id="ExampleChannel005.epg-pw"><display-name>ExampleChannel005</display-name></channel>
  <programme channel="ExampleChannel005.epg-pw" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
                    "https://example.test/public-catalog.xml.gz": b"""<tv>
  <channel id="ExampleChannel005.br"><display-name>ExampleChannel005</display-name></channel>
  <programme channel="ExampleChannel005.br" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
                }
                return gzip.compress(payloads[url])

        with patch(
            "legal_iptv.services.epg_mapper.EPG_INDEX_URLS",
            [
                "https://example.test/epg-pw.xml.gz",
                "https://example.test/public-catalog.xml.gz",
            ],
        ):
            aliases = load_xmltv_aliases(FakeClient())

        self.assertEqual(aliases["examplechannel005"].tvg_id, "ExampleChannel005.epg-pw")

    def test_load_xmltv_aliases_falls_back_when_prior_source_has_no_programming(self):
        class FakeClient:
            def get_bytes(self, url: str) -> bytes:
                payloads = {
                    "https://example.test/epg-pw.xml.gz": b"""<tv>
  <channel id="ExampleChannel005.epg-pw"><display-name>ExampleChannel005</display-name></channel>
</tv>""",
                    "https://example.test/public-catalog.xml.gz": b"""<tv>
  <channel id="ExampleChannel005.br"><display-name>ExampleChannel005</display-name></channel>
  <programme channel="ExampleChannel005.br" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
                }
                return gzip.compress(payloads[url])

        with patch(
            "legal_iptv.services.epg_mapper.EPG_INDEX_URLS",
            [
                "https://example.test/epg-pw.xml.gz",
                "https://example.test/public-catalog.xml.gz",
            ],
        ):
            aliases = load_xmltv_aliases(FakeClient())

        self.assertEqual(aliases["examplechannel005"].tvg_id, "ExampleChannel005.br")

    def test_load_xmltv_aliases_uses_fresh_cache(self):
        class FailingClient:
            def get_bytes(self, url: str) -> bytes:
                raise AssertionError("fresh cache should skip network")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "epg-cache.json"
            cache_file.write_text(
                json.dumps(
                    {
                        "generated_at": "2999-01-01T00:00:00+00:00",
                        "aliases": {
                            "examplechannel005": {
                                "tvg_id": "ExampleChannel005.cached",
                                "display_name": "ExampleChannel005",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            aliases = load_xmltv_aliases(FailingClient(), cache_file=cache_file)

        self.assertEqual(aliases["examplechannel005"].tvg_id, "ExampleChannel005.cached")
        self.assertEqual(aliases["examplechannel005"].display_name, "ExampleChannel005")

    def test_load_xmltv_aliases_writes_cache_from_sources(self):
        class FakeClient:
            def get_bytes(self, url: str) -> bytes:
                payload = b"""<tv>
  <channel id="ExampleChannel005.epg-pw"><display-name>ExampleChannel005</display-name></channel>
  <programme channel="ExampleChannel005.epg-pw" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>"""
                return gzip.compress(payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "epg-cache.json"
            with patch(
                "legal_iptv.services.epg_mapper.EPG_INDEX_URLS",
                ["https://example.test/epg-pw.xml.gz"],
            ):
                aliases = load_xmltv_aliases(FakeClient(), cache_file=cache_file)

            cache_payload = json.loads(cache_file.read_text(encoding="utf-8"))

        self.assertEqual(aliases["examplechannel005"].tvg_id, "ExampleChannel005.epg-pw")
        self.assertEqual(cache_payload["aliases"]["examplechannel005"]["tvg_id"], "ExampleChannel005.epg-pw")

    def test_load_xmltv_aliases_uses_stale_cache_when_sources_fail(self):
        class FailingClient:
            def get_bytes(self, url: str) -> bytes:
                raise RuntimeError("network failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "epg-cache.json"
            cache_file.write_text(
                json.dumps(
                    {
                        "generated_at": "2000-01-01T00:00:00+00:00",
                        "aliases": {
                            "examplechannel005": {
                                "tvg_id": "ExampleChannel005.stale",
                                "display_name": None,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "legal_iptv.services.epg_mapper.EPG_INDEX_URLS",
                ["https://example.test/epg-pw.xml.gz"],
            ):
                aliases = load_xmltv_aliases(
                    FailingClient(),
                    cache_file=cache_file,
                    cache_ttl_seconds=1,
                )

        self.assertEqual(aliases["examplechannel005"].tvg_id, "ExampleChannel005.stale")

    def test_maps_generic_live_catalog_channel_from_public_catalog_name(self):
        live_channel = make_channel(
            id="script_catalog_1.41",
            name="ExampleChannel005",
            source="live_stream_catalog",
            tvg_id="script_catalog_1.41",
        )
        iptv_channel = make_channel(
            id="ExampleChannel005.br",
            name="ExampleChannel005",
            source="public_catalog",
            tvg_id="ExampleChannel005.br",
        )

        enriched = enrich_epg_metadata([live_channel, iptv_channel])

        self.assertEqual(enriched[0].tvg_id, "ExampleChannel005.br")
        self.assertEqual(enriched[0].name, "ExampleChannel005")

    def test_maps_slug_from_manual_aliases(self):
        channel = make_channel(
            id="script_catalog_1.10",
            name="Example Channel 008",
            source="live_stream_catalog",
            tvg_id="ExampleChannel008",
        )

        enriched = enrich_epg_metadata([channel])

        self.assertEqual(enriched[0].tvg_id, "ExampleChannel008.br")
        self.assertEqual(enriched[0].name, "Example Channel 008")

    def test_maps_regional_alias_without_source_suffix(self):
        channel = make_channel(
            id="script_catalog_1.50",
            name="Example Channel 001",
            source="live_stream_catalog",
        )

        enriched = enrich_epg_metadata([channel])

        self.assertEqual(enriched[0].tvg_id, "ExampleChannel001.br")
        self.assertEqual(enriched[0].name, "Example Channel 001")

    def test_maps_user_approved_aliases(self):
        approved = {
            "ExampleChannel013": "ExampleChannel013",
            "Mature Animation": "ExampleChannel014",
            "Example Channel 015": "ExampleChannel015",
            "Example Channel 016": "ExampleChannel016",
        }

        for index, (name, tvg_id) in enumerate(approved.items()):
            with self.subTest(name=name):
                channel = make_channel(
                    id=f"approved.{index}",
                    name=name,
                    source="live_stream_catalog",
                )

                enriched = enrich_epg_metadata([channel])

                self.assertEqual(enriched[0].tvg_id, tvg_id)
                self.assertEqual(enriched[0].name, name)

    def test_does_not_assign_international_guide_to_unknown_regional_feed(self):
        channels = [
            make_channel(
                id="networkexample.unknown",
                name="NetworkExample Afiliada Sem EPG",
                source="live_stream_catalog",
            ),
            make_channel(
                id="record.unknown",
                name="Network ThreeTV Afiliada Sem EPG",
                source="live_stream_catalog",
            ),
        ]

        enriched = enrich_epg_metadata(channels)

        self.assertIsNone(enriched[0].tvg_id)
        self.assertIsNone(enriched[1].tvg_id)

    def test_roraima_does_not_receive_an_unverified_national_schedule(self):
        channel = make_channel(
            id="record.roraima",
            name="Network ThreeTV Roraima",
            source="live_stream_catalog",
        )

        enriched = enrich_epg_metadata([channel])

        self.assertIsNone(enriched[0].tvg_id)

    def test_maps_name_from_xmltv_aliases(self):
        channel = make_channel(
            id="script_catalog_1.52",
            name="FreePlatform Example Channel 120 Sucessos",
            source="live_stream_catalog",
        )
        aliases = {
            "freeplatform example channel 120 sucessos": parse_xmltv_aliases(
                b"""<tv><channel id="free_platform-cine-sucessos"><display-name>FreePlatform Example Channel 120 Sucessos</display-name></channel></tv>"""
            )["freeplatform example channel 120 sucessos"]
        }

        enriched = enrich_epg_metadata([channel], xmltv_aliases=aliases)

        self.assertEqual(enriched[0].tvg_id, "free_platform-cine-sucessos")

    def test_xmltv_aliases_have_priority_over_manual_aliases(self):
        channel = make_channel(
            id="script_catalog_1.41",
            name="ExampleChannel005",
            source="live_stream_catalog",
        )
        aliases = {
            "examplechannel005": parse_xmltv_aliases(
                b"""<tv><channel id="ExampleChannel005.epg-pw"><display-name>ExampleChannel005</display-name></channel></tv>"""
            )["examplechannel005"]
        }

        enriched = enrich_epg_metadata([channel], xmltv_aliases=aliases)

        self.assertEqual(enriched[0].tvg_id, "ExampleChannel005.epg-pw")

    def test_matches_variant_name_without_quality_suffix(self):
        channel = make_channel(
            id="addon.channel.4k",
            name="Example Channel [4K] [HEVC]",
            source="live_stream_catalog",
        )
        aliases = {
            "example channel": parse_xmltv_aliases(
                b'<tv><channel id="Example.br"><display-name>Example Channel</display-name></channel></tv>'
            )["example channel"]
        }

        enriched = enrich_epg_metadata([channel], xmltv_aliases=aliases)

        self.assertEqual(enriched[0].tvg_id, "Example.br")

    def test_variants_share_primary_logical_channel_tvg_id(self):
        aliases = parse_xmltv_aliases(
            b"""<tv>
  <channel id="2ExampleChannel048"><display-name>PremiumExample Channel</display-name></channel>
  <channel id="2445"><display-name>PremiumExample Channel HD</display-name></channel>
  <programme channel="2ExampleChannel048" start="20260714000000 +0000" stop="20260714010000 +0000"/>
  <programme channel="2445" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
            require_programmes=True,
        )
        channels = [
            make_channel(
                id="premiumexample.fhd",
                name="PremiumExample Channel [FHD]",
                source="live_stream_catalog",
            ),
            make_channel(
                id="premiumexample.sd",
                name="PremiumExample Channel [SD]",
                source="live_stream_catalog",
            ),
        ]

        enriched = enrich_epg_metadata(channels, xmltv_aliases=aliases)

        self.assertEqual({channel.tvg_id for channel in enriched}, {"2ExampleChannel048"})

    def test_clears_unreliable_tvg_id_when_no_mapping_exists(self):
        channel = make_channel(
            id="script_catalog_1.99",
            name="Unknown Web Channel",
            source="live_stream_catalog",
            tvg_id="unknown-web-channel",
        )

        enriched = enrich_epg_metadata([channel])

        self.assertIsNone(enriched[0].tvg_id)


if __name__ == "__main__":
    unittest.main()
