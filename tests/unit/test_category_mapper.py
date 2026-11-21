import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from legal_iptv.exporters import render_m3u
from legal_iptv.models import Channel
from legal_iptv.services.category_mapper import (
    canonical_category_name,
    localized_category_name,
)
from legal_iptv.services.companion import select_companion_channels
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.sources.live_stream_catalog import fetch_channels


class CategoryMapperTest(unittest.TestCase):
    def test_merges_source_aliases_and_preserves_distinct_genres(self):
        groups = {
            "Infantil": ["kids", "infantil", "Infantis", "  INFANTIS  "],
            "Notícias": ["news", "Notícia", "Notícias", " NOTICIAS "],
            "Religião": ["religious", "Religião", "Religioso", "Religiosos"],
            "Variedades": ["general", "PUBLIC", "Recomendados", "recommended"],
            "TV Aberta": ["tv aberta", "TV   ABERTA", "aberto", "Canais Abertos"],
            "Animação": ["animation", "Animacao"],
            "Cultura": ["culture", "Cultura"],
            "Educação": ["education", "Educação"],
            "Internacional": ["Internacional"],
        }
        for expected, aliases in groups.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(localized_category_name(alias), expected)
                    self.assertEqual(canonical_category_name(alias), expected)
        self.assertEqual(localized_category_name("Unknown"), "Variedades")
        self.assertEqual(localized_category_name(None), "Variedades")
        self.assertEqual(localized_category_name("  "), "Variedades")
        self.assertEqual(canonical_category_name("  Custom   Genre "), "Custom Genre")

    def test_ingestion_selection_and_companion_render_the_same_canonical_groups(self):
        aliases = ["kids", "Infantis", "Notícia", "Notícias", "Religiosos",
                   "religious", "Recomendados", "public", "TV Aberta"]
        raw = [{"id": str(index), "name": f"Test Channel {index}", "group": group,
                "stream_url": f"https://example.test/{index}.m3u8",
                "status": "resolved", "provider_id": "directory_catalog"}
               for index, group in enumerate(aliases)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(raw))
            client = Mock()
            candidates = fetch_channels(client, min_live_ttl=900, local_file=path)
            client.get_json.assert_not_called()
        configuration = load_playlist_configuration()
        selected = select_profile_channels(candidates, configuration, "full")
        companion, excluded = select_companion_channels(
            selected, {"base_reference_channels": []}, preselected=True)
        self.assertEqual(len(selected), len(raw))
        self.assertEqual(excluded, [])
        self.assertEqual({item.group for item in selected}, {
            "Infantil", "Notícias", "Religião", "Variedades", "TV Aberta"})
        self.assertEqual({item.group for item in selected}, {item.group for item in companion})
        for channels in (selected, companion):
            playlist = render_m3u(channels, category_order=configuration.group_order)
            self.assertEqual(playlist.count("#EXTINF:"), len(raw))
            for removed in ("Infantis", "Notícia", "Religiosos", "Recomendados", "public"):
                self.assertNotIn(f'group-title="{removed}"', playlist)
                self.assertNotIn(f"#EXTGRP:{removed}\n", playlist)
            self.assertEqual(playlist.count("### Canais Variedades\n"), 1)

    def test_profile_mapping_accepts_accents_case_and_whitespace_in_custom_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"profiles": {"full": {"selection_mode": "logical_provider"}},
                                        "category_map": {"  ESPECIAL   REGIÃO ": "Acervo Local"},
                                        "group_order": ["Notícia", "Notícias", "Recomendados", "Variedades"]}))
            configuration = load_playlist_configuration(path)
        self.assertEqual(configuration.group_order, ("Notícias", "Variedades"))
        candidate = Channel(id="one", name="One", stream_url="https://example.test/one.m3u8",
                            logo="", source="extra", group="Variedades", raw_group="especial regiao")
        selected = select_profile_channels([candidate], configuration, "full")
        self.assertEqual(selected[0].group, "Acervo Local")

    def test_cached_legacy_groups_are_normalized_even_without_profile_selection(self):
        aliases = ["Notícia", "Notícias", "Infantis", "Infantil", "Recomendados", "public"]
        channels = [Channel(id=str(index), name=f"Channel {index}", logo="", source="public_catalog",
                            stream_url=f"https://example.test/{index}.m3u8", group=group)
                    for index, group in enumerate(aliases)]
        for order in (None, aliases):
            playlist = render_m3u(channels, category_order=order)
            self.assertEqual(playlist.count("#EXTINF:"), len(channels))
            for group in ("Notícias", "Infantil", "Variedades"):
                self.assertEqual(playlist.count(f"### Canais {group}\n"), 1)
                self.assertEqual(playlist.count(f'group-title="{group}"'), 2)
        self.assertEqual([item.group for item in channels], aliases)


if __name__ == "__main__":
    unittest.main()
