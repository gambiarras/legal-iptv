import gzip
import json
import tempfile
import unittest
from importlib import resources
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
        resource = resources.files("legal_iptv.resources").joinpath(
            "epg_aliases.json"
        )
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
  <channel id="Band.br">
    <display-name>BR - Band</display-name>
    <display-name>Band</display-name>
  </channel>
</tv>
"""

        aliases = parse_xmltv_aliases(payload)

        self.assertEqual(aliases["band"].tvg_id, "Band.br")

    def test_parses_only_programmed_xmltv_channels_when_required(self):
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="Band.epg-pw">
    <display-name>Band</display-name>
  </channel>
  <channel id="Band.br">
    <display-name>Band</display-name>
  </channel>
  <programme channel="Band.br" start="20260714000000 +0000" stop="20260714010000 +0000">
    <title>Program</title>
  </programme>
</tv>
"""

        aliases = parse_xmltv_aliases(payload, require_programmes=True)

        self.assertEqual(aliases["band"].tvg_id, "Band.br")

    def test_duplicate_name_prefers_first_programmed_channel(self):
        payload = b"""<tv>
  <channel id="primary"><display-name>Warner Channel</display-name></channel>
  <channel id="fallback"><display-name>Warner Channel</display-name></channel>
  <programme channel="primary" start="20260714000000 +0000" stop="20260714010000 +0000"/>
  <programme channel="fallback" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>"""

        aliases = parse_xmltv_aliases(payload, require_programmes=True)

        self.assertEqual(aliases["warner channel"].tvg_id, "primary")

    def test_load_xmltv_aliases_prefers_first_source_with_programming(self):
        class FakeClient:
            def get_bytes(self, url: str) -> bytes:
                payloads = {
                    "https://example.test/epg-pw.xml.gz": b"""<tv>
  <channel id="Band.epg-pw"><display-name>Band</display-name></channel>
  <programme channel="Band.epg-pw" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
                    "https://example.test/iptv-org.xml.gz": b"""<tv>
  <channel id="Band.br"><display-name>Band</display-name></channel>
  <programme channel="Band.br" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
                }
                return gzip.compress(payloads[url])

        with patch(
            "legal_iptv.services.epg_mapper.EPG_INDEX_URLS",
            [
                "https://example.test/epg-pw.xml.gz",
                "https://example.test/iptv-org.xml.gz",
            ],
        ):
            aliases = load_xmltv_aliases(FakeClient())

        self.assertEqual(aliases["band"].tvg_id, "Band.epg-pw")

    def test_load_xmltv_aliases_falls_back_when_prior_source_has_no_programming(self):
        class FakeClient:
            def get_bytes(self, url: str) -> bytes:
                payloads = {
                    "https://example.test/epg-pw.xml.gz": b"""<tv>
  <channel id="Band.epg-pw"><display-name>Band</display-name></channel>
</tv>""",
                    "https://example.test/iptv-org.xml.gz": b"""<tv>
  <channel id="Band.br"><display-name>Band</display-name></channel>
  <programme channel="Band.br" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
                }
                return gzip.compress(payloads[url])

        with patch(
            "legal_iptv.services.epg_mapper.EPG_INDEX_URLS",
            [
                "https://example.test/epg-pw.xml.gz",
                "https://example.test/iptv-org.xml.gz",
            ],
        ):
            aliases = load_xmltv_aliases(FakeClient())

        self.assertEqual(aliases["band"].tvg_id, "Band.br")

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
                            "band": {
                                "tvg_id": "Band.cached",
                                "display_name": "Band",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            aliases = load_xmltv_aliases(FailingClient(), cache_file=cache_file)

        self.assertEqual(aliases["band"].tvg_id, "Band.cached")
        self.assertEqual(aliases["band"].display_name, "Band")

    def test_load_xmltv_aliases_writes_cache_from_sources(self):
        class FakeClient:
            def get_bytes(self, url: str) -> bytes:
                payload = b"""<tv>
  <channel id="Band.epg-pw"><display-name>Band</display-name></channel>
  <programme channel="Band.epg-pw" start="20260714000000 +0000" stop="20260714010000 +0000"/>
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

        self.assertEqual(aliases["band"].tvg_id, "Band.epg-pw")
        self.assertEqual(cache_payload["aliases"]["band"]["tvg_id"], "Band.epg-pw")

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
                            "band": {
                                "tvg_id": "Band.stale",
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

        self.assertEqual(aliases["band"].tvg_id, "Band.stale")

    def test_maps_generic_live_catalog_channel_from_iptv_org_name(self):
        live_channel = make_channel(
            id="script_catalog_1.41",
            name="Band",
            source="live_stream_catalog",
            tvg_id="script_catalog_1.41",
        )
        iptv_channel = make_channel(
            id="Band.br",
            name="Band",
            source="iptv_org",
            tvg_id="Band.br",
        )

        enriched = enrich_epg_metadata([live_channel, iptv_channel])

        self.assertEqual(enriched[0].tvg_id, "Band.br")
        self.assertEqual(enriched[0].name, "Band")

    def test_maps_slug_from_manual_aliases(self):
        channel = make_channel(
            id="script_catalog_1.10",
            name="TV Aparecida",
            source="live_stream_catalog",
            tvg_id="tv-aparecida",
        )

        enriched = enrich_epg_metadata([channel])

        self.assertEqual(enriched[0].tvg_id, "TVAparecida.br")
        self.assertEqual(enriched[0].name, "TV Aparecida")

    def test_maps_regional_alias_without_source_suffix(self):
        channel = make_channel(
            id="script_catalog_1.50",
            name="Globo SP",
            source="live_stream_catalog",
        )

        enriched = enrich_epg_metadata([channel])

        self.assertEqual(enriched[0].tvg_id, "TVGloboSaoPaulo.br")
        self.assertEqual(enriched[0].name, "Globo SP")

    def test_maps_user_approved_aliases(self):
        approved = {
            "AgroMais": "2353",
            "Adult Swim": "417185",
            "TV Gazeta SP": "523302",
            "Kenan e Kel": "5ffcc5130fd98c0007f2e216",
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

    def test_uses_configured_regional_fallback_prefixes(self):
        channels = [
            make_channel(
                id="globo.unknown",
                name="Globo Afiliada Sem EPG",
                source="live_stream_catalog",
            ),
            make_channel(
                id="record.unknown",
                name="RecordTV Afiliada Sem EPG",
                source="live_stream_catalog",
            ),
        ]

        enriched = enrich_epg_metadata(channels)

        self.assertEqual(enriched[0].tvg_id, "GloboInternacional.br")
        self.assertEqual(enriched[1].tvg_id, "RecordTVInternational.br")

    def test_exact_regional_alias_precedes_configured_prefix(self):
        channel = make_channel(
            id="record.roraima",
            name="RecordTV Roraima",
            source="live_stream_catalog",
        )

        enriched = enrich_epg_metadata([channel])

        self.assertEqual(enriched[0].tvg_id, "RecordTVBrasil.br")

    def test_maps_name_from_xmltv_aliases(self):
        channel = make_channel(
            id="script_catalog_1.52",
            name="Pluto TV Cine Sucessos",
            source="live_stream_catalog",
        )
        aliases = {
            "pluto tv cine sucessos": parse_xmltv_aliases(
                b"""<tv><channel id="pluto-cine-sucessos"><display-name>Pluto TV Cine Sucessos</display-name></channel></tv>"""
            )["pluto tv cine sucessos"]
        }

        enriched = enrich_epg_metadata([channel], xmltv_aliases=aliases)

        self.assertEqual(enriched[0].tvg_id, "pluto-cine-sucessos")

    def test_xmltv_aliases_have_priority_over_manual_aliases(self):
        channel = make_channel(
            id="script_catalog_1.41",
            name="Band",
            source="live_stream_catalog",
        )
        aliases = {
            "band": parse_xmltv_aliases(
                b"""<tv><channel id="Band.epg-pw"><display-name>Band</display-name></channel></tv>"""
            )["band"]
        }

        enriched = enrich_epg_metadata([channel], xmltv_aliases=aliases)

        self.assertEqual(enriched[0].tvg_id, "Band.epg-pw")

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
  <channel id="2438"><display-name>Warner Channel</display-name></channel>
  <channel id="2445"><display-name>Warner Channel HD</display-name></channel>
  <programme channel="2438" start="20260714000000 +0000" stop="20260714010000 +0000"/>
  <programme channel="2445" start="20260714000000 +0000" stop="20260714010000 +0000"/>
</tv>""",
            require_programmes=True,
        )
        channels = [
            make_channel(
                id="warner.fhd",
                name="Warner Channel [FHD]",
                source="live_stream_catalog",
            ),
            make_channel(
                id="warner.sd",
                name="Warner Channel [SD]",
                source="live_stream_catalog",
            ),
        ]

        enriched = enrich_epg_metadata(channels, xmltv_aliases=aliases)

        self.assertEqual({channel.tvg_id for channel in enriched}, {"2438"})

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
