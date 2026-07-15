import gzip
import json
import tempfile
import unittest
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
