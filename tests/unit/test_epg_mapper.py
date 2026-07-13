import unittest

from legal_iptv.models import Channel
from legal_iptv.services.epg_mapper import enrich_epg_metadata, parse_xmltv_aliases


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
