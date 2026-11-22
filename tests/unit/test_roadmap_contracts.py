import hashlib
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from legal_iptv.local_config import (
    configuration_snapshot,
    environment_value,
    load_json_file,
    load_resource,
    local_config_dir,
    runtime_value,
)
from legal_iptv.services.aggregation_phases import (
    DecisionLedger,
    prepare_candidates,
    select_candidates,
    validate_candidates,
)
from legal_iptv.services.channel_identity import canonicalize_channels
from legal_iptv.services.companion import (
    enrich_companion_candidates,
    select_companion_channels,
)
from legal_iptv.services.epg_mapper import EPGMapping
from legal_iptv.services.epg_providers import EpgProvider
from legal_iptv.services.guide import coverage_diagnostics, ensure_guide, project_guide
from legal_iptv.services.metadata import build_run_metadata
from legal_iptv.services.profile_config import load_playlist_configuration
from legal_iptv.services.profile_selector import select_profile_channels
from legal_iptv.sources.live_stream_catalog import fetch_channels
from tests.unit.test_guide_projection import guide
from tests.unit.test_media_proof import channel


class FeedIdentityTest(unittest.TestCase):
    def test_same_name_shared_epg_id_and_same_transport_do_not_collapse_feeds(self):
        candidates = [
            channel(
                id="same", name="Example Network", tvg_id="Generic.br", feed_id=feed
            )
            for feed in ("region-a", "region-b")
        ]
        winners = select_profile_channels(
            candidates, load_playlist_configuration(), "full"
        )
        self.assertEqual(len(winners), 2)
        self.assertEqual(len({item.id for item in winners}), 2)
        self.assertEqual(len({item.tvg_id for item in winners}), 2)
        self.assertTrue(all(item.epg_id_ambiguous for item in winners))
        aliases = {"example network": EPGMapping("Generic.br")}
        for current in winners:
            remapped = canonicalize_channels(
                enrich_companion_candidates([current], aliases)
            )[0]
            self.assertEqual(remapped.tvg_id, current.tvg_id)
            self.assertEqual(remapped.logical_channel_id, current.logical_channel_id)
        companion, _ = select_companion_channels(
            winners, {"base_reference_channels": []}, preselected=True
        )
        self.assertEqual(
            {item.tvg_id for item in companion}, {item.tvg_id for item in winners}
        )

    def test_same_feed_variants_with_different_labels_compete_and_unique_epg_is_preserved(
        self,
    ):
        candidates = [
            channel(
                id="low",
                name="Example Region",
                feed_id="region-a",
                tvg_id="RegionA.br",
                resolution="720p",
            ),
            channel(
                id="high",
                name="Other Label",
                feed_id="region-a",
                tvg_id="RegionA.br",
                resolution="1080p",
            ),
        ]
        winners = select_profile_channels(
            candidates, load_playlist_configuration(), "full"
        )
        self.assertEqual([item.id for item in winners], ["high"])
        self.assertEqual(winners[0].tvg_id, "RegionA.br")
        self.assertFalse(winners[0].epg_id_ambiguous)

    def test_feed_specific_epg_ids_survive_a_shared_display_alias(self):
        candidates = [
            channel(id=region, name="Network", feed_id=region, tvg_id=region + ".br")
            for region in ("North", "South")
        ]
        enriched = enrich_companion_candidates(
            candidates, {"network": EPGMapping("Generic.br")}
        )
        self.assertEqual([item.tvg_id for item in enriched], ["North.br", "South.br"])
        canonical = canonicalize_channels(enriched)
        self.assertTrue(all(not item.epg_id_ambiguous for item in canonical))

    def test_global_feed_cross_provider_and_reviewed_feed_aliases_compete(self):
        candidates = [
            channel(
                id="first",
                name="One Label",
                feed_id="global-feed",
                provider_id="directory_catalog",
            ),
            channel(
                id="second",
                name="Other Label",
                feed_id="global-feed",
                provider_id="extra",
            ),
        ]
        winners = select_profile_channels(
            candidates, load_playlist_configuration(), "full"
        )
        self.assertEqual(len(winners), 1)

        def setting(key, default=None):
            return (
                {"provider-local:one:north": "global-feed"}
                if key == "feed_id_aliases"
                else default
            )

        candidates[0] = replace(candidates[0], feed_id="provider-local:one:north")
        with patch(
            "legal_iptv.services.channel_identity.runtime_value", side_effect=setting
        ):
            winners = select_profile_channels(
                candidates, load_playlist_configuration(), "full"
            )
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].feed_id, "global-feed")

    def test_named_regional_fallback_without_feed_stays_distinct(self):
        candidates = [
            channel(id=name, name=name, tvg_id="Generic.br")
            for name in ("Network North", "Network South")
        ]
        winners = select_profile_channels(
            candidates, load_playlist_configuration(), "full"
        )
        self.assertEqual(len(winners), 2)
        self.assertEqual(len({item.tvg_id for item in winners}), 2)


class EditorialCategoriesTest(unittest.TestCase):
    def test_explicit_override_wins_identity_tags_and_raw_map(self):
        config = replace(
            load_playlist_configuration(),
            category_map={"general": "Variedades"},
            category_overrides={"example": "Esportes"},
            category_rules=(
                {"id": "tag-news", "category": "Notícias", "all_tags": ["journalism"]},
            ),
            overrides={"example": {"group": "Infantis"}},
        )
        winner = select_profile_channels(
            [channel(raw_group="general", tags=["journalism"])], config, "full"
        )[0]
        self.assertEqual(
            (winner.group, winner.category_origin, winner.category_reason),
            ("Infantil", "override", "explicit_group_override"),
        )

    def test_tag_priority_identity_and_conflicts_are_explained(self):
        original = channel(raw_group="general", tags=["journalism", "local"])
        rules = (
            {
                "id": "news",
                "category": "Notícias",
                "all_tags": ["journalism"],
                "any_tags": ["local"],
                "priority": 5,
            },
            {
                "id": "sport",
                "category": "Esportes",
                "any_tags": ["local"],
                "priority": 2,
            },
        )
        config = replace(load_playlist_configuration(), category_rules=rules)
        winner = select_profile_channels([original], config, "full")[0]
        self.assertEqual(
            (winner.group, winner.category_origin, winner.category_rule),
            ("Notícias", "tags", "news"),
        )
        config = replace(config, category_overrides={"example": "Cultura"})
        winner = select_profile_channels([original], config, "full")[0]
        self.assertEqual(
            (winner.group, winner.category_origin), ("Cultura", "identity")
        )
        config = replace(
            config,
            category_overrides={},
            category_rules=rules
            + (
                {
                    "id": "tie",
                    "category": "Esportes",
                    "any_tags": ["local"],
                    "priority": 5,
                },
            ),
        )
        winner = select_profile_channels([original], config, "full")[0]
        self.assertEqual(
            (winner.group, winner.category_origin, winner.category_reason),
            ("Variedades", "review", "conflicting_tag_rules"),
        )


class ConfigurationAndContractTest(unittest.TestCase):
    def test_runtime_resources_environment_and_endpoint_stay_fixed_until_next_round(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ, {"IPTV_LOCAL_CONFIG_DIR": directory, "ROUND_VALUE": "first"}
            ),
        ):
            root = Path(directory)
            (root / "resources").mkdir()
            runtime = root / "runtime.json"
            resource = root / "resources" / "extra_channels.json"
            runtime.write_text(
                json.dumps(
                    {
                        "catalog_url": "https://first.example.invalid/catalog",
                        "roles": ["first"],
                    }
                )
            )
            resource.write_text('[{"id":"first"}]')
            client = Mock()
            client.get_json.return_value = []
            with configuration_snapshot():
                runtime.write_text(
                    json.dumps(
                        {
                            "catalog_url": "https://second.example.invalid/catalog",
                            "roles": ["second"],
                        }
                    )
                )
                resource.write_text('[{"id":"second"}]')
                os.environ["ROUND_VALUE"] = "second"
                fetched = runtime_value("roles")
                fetched.append("mutated")
                self.assertEqual(runtime_value("roles"), ["first"])
                self.assertEqual(
                    load_resource("extra_channels.json"), [{"id": "first"}]
                )
                self.assertEqual(environment_value("ROUND_VALUE"), "first")
                fetch_channels(client, 900)
                client.get_json.assert_called_with(
                    "https://first.example.invalid/catalog"
                )
            with configuration_snapshot():
                fetch_channels(client, 900)
                client.get_json.assert_called_with(
                    "https://second.example.invalid/catalog"
                )
                self.assertEqual(
                    load_resource("extra_channels.json"), [{"id": "second"}]
                )

    def test_nested_configuration_context_and_explicit_profile_stay_isolated(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"IPTV_LOCAL_CONFIG_DIR": directory}),
        ):
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text('{"value":"first"}')
            with configuration_snapshot((profile,)) as outer:
                profile.write_text('{"value":"second"}')
                with configuration_snapshot() as inner:
                    self.assertIs(inner, outer)
                    self.assertEqual(local_config_dir(), root)
                    self.assertEqual(load_json_file(profile), {"value": "first"})
            self.assertEqual(load_json_file(profile), {"value": "second"})
            self.assertEqual(runtime_value("unknown", "fallback"), "fallback")
            (root / "runtime.json").write_text("[]")
            with self.assertRaises(ValueError), configuration_snapshot():
                pass
            with self.assertRaises(ValueError):
                runtime_value("unknown")

    def test_exact_catalog_bytes_and_additive_contract_include_ingestion_losses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            raw = b'\xef\xbb\xbf [ {"id":"example", "name":"Example", "stream_url":"https://example.test/live.m3u8", "status":"resolved", "tags":["local"], "feed_id":"region-a"}, {"status":"removed"}, null ]\n'
            path.write_bytes(raw)
            provenance = {}
            selected = fetch_channels(Mock(), 900, path, provenance=provenance)
            self.assertEqual(
                provenance["catalog_input_sha256"], hashlib.sha256(raw).hexdigest()
            )
            self.assertEqual(
                provenance["ingestion_counts"], {"removed": 1, "invalid_record": 1}
            )
            self.assertEqual(
                (selected[0].feed_id, selected[0].tags), ("region-a", ["local"])
            )
            meta = build_run_metadata(
                selected,
                selected,
                catalog_input_sha256=provenance["catalog_input_sha256"],
            ).to_dict()
            self.assertEqual(meta["contract_version"], 1)
            self.assertEqual(
                meta["catalog_input_sha256"], hashlib.sha256(raw).hexdigest()
            )

    def test_phase_losses_are_disjoint_bounded_and_sanitized(self):
        candidates = [channel(id=str(index)) for index in range(5)]
        candidates[0] = replace(
            candidates[0], id="https://private.example.invalid/credential"
        )
        ledger = DecisionLedger(limit=2)
        eligible = prepare_candidates(
            candidates,
            lambda item: "adult" if item is candidates[0] else None,
            lambda items: items,
            ledger,
        )
        validated = validate_candidates(
            eligible.channels, lambda items: items[1:], lambda items: items[1:], ledger
        )
        winners = select_candidates(
            validated.channels,
            lambda items: items[:1],
            lambda item: "deduplicated",
            ledger,
        )
        self.assertEqual(
            dict(ledger.counts),
            {"adult": 1, "unavailable": 1, "media_unconfirmed": 1, "deduplicated": 1},
        )
        self.assertEqual((len(ledger.decisions), ledger.truncated), (2, 2))
        self.assertNotIn("private.example", json.dumps(ledger.decisions))
        meta = build_run_metadata(
            candidates, winners.channels, decision_counts=dict(ledger.counts)
        ).to_dict()
        self.assertEqual(meta["deduplicated_channels"], 1)


class TargetedGuideTest(unittest.TestCase):
    def test_fresh_partial_refresh_targets_scoped_provider_preserves_collection_time_and_reports(
        self,
    ):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.xml"
            diagnostics = path.with_suffix(".json")
            ET.ElementTree(guide(["Existing.br"], now)).write(path)
            old_ns = path.stat().st_mtime_ns - 90_000_000_000
            os.utime(path, ns=(old_ns, old_ns))
            target = EpgProvider(
                "target",
                "https://target.example.invalid/guide",
                channel_ids=("New.br",),
            )
            unrelated = EpgProvider(
                "unrelated",
                "https://other.example.invalid/guide",
                channel_ids=("Other.br",),
            )
            broad = EpgProvider("broad", "https://broad.example.invalid/guide")
            progress = []

            def fetch(provider, client, **kwargs):
                self.assertEqual(provider.id, "target")
                self.assertEqual(kwargs["channel_ids"], {"New.br"})
                return guide(["New.br"], now)

            with patch.object(
                EpgProvider, "fetch", autospec=True, side_effect=fetch
            ) as collected:
                ensure_guide(
                    Mock(),
                    output_path=path,
                    diagnostics_path=diagnostics,
                    ttl_seconds=3600,
                    lkg_seconds=7200,
                    min_coverage_ratio=0.5,
                    providers=[target, unrelated, broad],
                    selected_channel_ids={"Existing.br", "New.br"},
                    required_channel_ids=("New.br",),
                    on_progress=lambda phase, details: progress.append(
                        (phase, details)
                    ),
                )
            self.assertEqual(collected.call_count, 1)
            self.assertEqual(path.stat().st_mtime_ns, old_ns)
            payload = json.loads(diagnostics.read_text())
            self.assertTrue(payload["targeted_refresh"])
            self.assertEqual(
                payload["summary"]["status_counts"], {"current_and_future": 2}
            )
            self.assertEqual(
                payload["coverage"]["New.br"]["status"], "current_and_future"
            )
            self.assertEqual([phase for phase, _ in progress], ["planned", "provider"])

    def test_critical_internal_gap_triggers_only_its_scoped_provider(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.xml"
            original = guide(["Critical.br"], now)
            future = original.findall("programme")[1]
            future.set(
                "start",
                (now + timedelta(hours=1, minutes=30)).strftime("%Y%m%d%H%M%S %z"),
            )
            ET.ElementTree(original).write(path)
            target = EpgProvider(
                "target",
                "https://target.example.invalid/guide",
                channel_ids=("Critical.br",),
            )
            other = EpgProvider(
                "other",
                "https://other.example.invalid/guide",
                channel_ids=("Other.br",),
            )

            def setting(key, default=None):
                return ["Critical.br"] if key == "epg_critical_channel_ids" else default

            with (
                patch("legal_iptv.services.guide.runtime_value", side_effect=setting),
                patch.object(
                    EpgProvider,
                    "fetch",
                    autospec=True,
                    return_value=guide(["Critical.br"], now),
                ) as fetched,
            ):
                ensure_guide(
                    Mock(),
                    output_path=path,
                    diagnostics_path=path.with_suffix(".json"),
                    ttl_seconds=3600,
                    lkg_seconds=7200,
                    min_coverage_ratio=0.5,
                    providers=[target, other],
                    selected_channel_ids={"Critical.br"},
                )
            self.assertEqual(fetched.call_count, 1)
            self.assertEqual(fetched.call_args.args[0].id, "target")
            self.assertEqual(
                json.loads(path.with_suffix(".json").read_text())["coverage"][
                    "Critical.br"
                ]["internal_gap_count"],
                0,
            )

    def test_local_projection_updates_diagnostic_without_fetch_or_observation_time(
        self,
    ):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.xml"
            diagnostics = path.with_suffix(".json")
            ET.ElementTree(guide(["Existing.br", "Removed.br"], now)).write(path)
            old_ns = path.stat().st_mtime_ns - 123456789
            os.utime(path, ns=(old_ns, old_ns))
            diagnostics.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "original", "collected_at": "original-observation"}
                        ]
                    }
                )
            )
            project_guide(
                path,
                path,
                {"Existing.br", "New.br"},
                now=now,
                channel_names={"New.br": "New Display"},
                diagnostics_path=diagnostics,
                require_coverage=False,
                required_channel_ids=("Existing.br",),
            )
            payload = json.loads(diagnostics.read_text())
            self.assertEqual(path.stat().st_mtime_ns, old_ns)
            self.assertEqual(payload["summary"]["total_channels"], 2)
            self.assertEqual(payload["coverage"]["New.br"]["status"], "missing")
            self.assertNotIn("Removed.br", payload["coverage"])
            self.assertEqual(
                payload["providers"][0]["collected_at"], "original-observation"
            )
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                project_guide(
                    path,
                    path,
                    {"Existing.br", "New.br"},
                    now=now,
                    require_coverage=False,
                    required_channel_ids=("New.br",),
                )
            self.assertEqual(path.read_bytes(), before)

    def test_summary_complete_while_coverage_is_bounded_and_required_first(self):
        now = datetime.now(timezone.utc)
        with patch("legal_iptv.services.guide.runtime_value", return_value=1):
            payload = coverage_diagnostics(
                guide(["Existing.br"], now),
                {"Existing.br", "Missing.br"},
                required_ids=("Missing.br",),
                now=now,
            )
        self.assertEqual(payload["summary"]["total_channels"], 2)
        self.assertEqual(payload["summary"]["required_missing"], 1)
        self.assertEqual(payload["coverage_truncated"], 1)
        self.assertEqual(list(payload["coverage"]), ["Missing.br"])
