import json
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from urllib3.exceptions import ReadTimeoutError

from legal_iptv.exporters import render_m3u
from legal_iptv.models.stream_validation import UrlValidation
from legal_iptv.services.aggregate import _required_media_proof, run_aggregation
from legal_iptv.services.channel_identity import load_policy
from legal_iptv.services.companion import _transport_safe, select_companion_channels
from legal_iptv.services.public_media_transport import is_public_media_transport
from legal_iptv.services.publication import is_exportable
from legal_iptv.services.selection_proof import verify_selection_proof
from legal_iptv.services.validation_schedule import (
    ValidationPolicy,
    context_for_channel,
)
from legal_iptv.sources.curated_text import (
    CuratedTextUnavailable,
    fetch_channels,
    load_sources,
    parse_source,
)
from tests.unit.test_aggregate import make_config
from tests.unit.test_media_proof import success_record

NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)
TOKEN = "a" * 32
URL = "https://media.example.invalid/exact/index.m3u8?token=" + TOKEN
ENTRY = {
    "match_name": "Remote Sample",
    "id": "extra.sample",
    "name": "Sample",
    "tvg_id": "Sample.br",
    "group": "Variedades",
    "media_host": "media.example.invalid",
    "media_path": "/exact/index.m3u8",
}
SOURCE = {
    "id": "curated_1",
    "url": "https://feed.example.invalid/channels.txt",
    "entries": [ENTRY],
}
RULE = {
    "channel_id": "extra.sample",
    "source": "extra",
    "source_type": "curated_text",
    "host": "media.example.invalid",
    "path": "/exact/index.m3u8",
    "query_parameters": {"token": {"format": "hex", "length": 32}},
}
TEXT = (
    "canal: Unselected\nfonte: https://unreviewed.example.invalid/stream\nlogo:\ncategoria: Other\n\n"
    "canal: Remote Sample\nfonte: "
    + URL
    + "\nlogo: https://images.example.invalid/sample.png\ncategoria: Remote Category\n"
)


class RawResponse:
    def __init__(self, response):
        self.response = response
        self.iterator = None

    def read1(self, _size, *, decode_content=False):
        if self.iterator is None:
            self.iterator = iter(self.response.iter_content(65536))
        return next(self.iterator, b"")

    def reset(self):
        self.iterator = None


class CuratedTextTest(unittest.TestCase):
    def setUp(self):
        self.sources = [dict(SOURCE)]
        self.rules = [dict(RULE)]

        def setting(key, default=None):
            return {
                "curated_text_sources": self.sources,
                "public_media_rules": self.rules,
            }.get(key, default)

        for target in (
            "legal_iptv.sources.curated_text.runtime_value",
            "legal_iptv.services.public_media_transport.runtime_value",
        ):
            active = patch(target, side_effect=setting)
            active.start()
            self.addCleanup(active.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name) / ".cache" / "curated-text"
        self.client = Mock(timeout=20)
        self.response = Mock(status_code=200)
        self.response.raw = RawResponse(self.response)
        self.response.close.side_effect = self.response.raw.reset
        self.response.iter_content.side_effect = lambda _: iter([TEXT.encode()])
        self.client.session.get.return_value = self.response

    def fetch(self, **kwargs):
        return fetch_channels(self.client, cache_dir=self.cache, now=NOW, **kwargs)

    def test_only_exact_allowlist_entries_are_imported_with_configured_identity_and_no_fake_expiry(
        self,
    ):
        channels = self.fetch()
        self.assertEqual(len(channels), 1)
        channel = channels[0]
        self.assertEqual(
            (channel.id, channel.name, channel.tvg_id, channel.group),
            ("extra.sample", "Sample", "Sample.br", "Variedades"),
        )
        self.assertEqual(channel.stream_url, URL)
        self.assertIsNone(channel.expires_at)
        self.assertIsNone(channel.ttl_seconds)
        self.assertIsNone(channel.resolved_at)
        self.assertEqual(
            (channel.publishable_static, channel.requires_dynamic_resolution),
            (True, False),
        )
        self.assertTrue(is_exportable(channel, "portable"))
        self.response.close.assert_called_once()
        self.assertTrue(self.client.session.get.call_args.kwargs["stream"])
        self.assertFalse(self.client.session.get.call_args.kwargs["allow_redirects"])
        path = self.cache / "curated_1.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(set(json.loads(path.read_text())["entries"]), {"extra.sample"})

    def test_health_and_fresh_cache_do_no_requests_and_stale_lkg_never_retimestamps(
        self,
    ):
        self.fetch()
        path = self.cache / "curated_1.json"
        original = (path.read_bytes(), path.stat().st_mtime_ns)
        self.client.session.get.reset_mock()
        self.fetch(cache_only=True)
        self.fetch()
        self.client.session.get.assert_not_called()
        self.client.session.get.side_effect = requests.HTTPError("unavailable")
        kept = fetch_channels(
            self.client, cache_dir=self.cache, now=NOW + timedelta(seconds=1801)
        )
        self.assertEqual(kept[0].stream_url, URL)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)
        self.client.session.get.reset_mock()
        fetch_channels(
            self.client,
            cache_dir=self.cache,
            now=NOW + timedelta(hours=5),
            cache_only=True,
        )
        self.client.session.get.assert_not_called()
        with self.assertRaises(CuratedTextUnavailable):
            fetch_channels(
                self.client,
                cache_dir=self.cache,
                now=NOW + timedelta(seconds=21601),
                cache_only=True,
            )

    def test_partial_duplicate_invalid_or_missing_allowlist_preserves_complete_lkg(
        self,
    ):
        self.fetch()
        path = self.cache / "curated_1.json"
        original = path.read_bytes()
        for text in (
            TEXT.replace("canal: Remote Sample", "canal: Missing"),
            TEXT + TEXT,
            TEXT.replace(
                "fonte: " + URL, "fonte: https://evil.example.invalid/live.m3u8"
            ),
            TEXT.replace("categoria: Remote Category", ""),
            TEXT.replace("token=" + TOKEN, "token=invalid"),
        ):
            with self.subTest(text=text[:20]):
                self.response.iter_content.side_effect = lambda _, value=text: iter(
                    [value.encode()]
                )
                selected = fetch_channels(
                    self.client, cache_dir=self.cache, now=NOW + timedelta(seconds=1801)
                )
                self.assertEqual(selected[0].stream_url, URL)
                self.assertEqual(path.read_bytes(), original)

    def test_missing_cache_configuration_changes_and_download_limits_fail_closed(self):
        with self.assertRaises(CuratedTextUnavailable):
            self.fetch(cache_only=True)
        self.client.session.get.assert_not_called()
        self.fetch()
        self.sources = [{**SOURCE, "entries": [{**ENTRY, "name": "New Name"}]}]
        self.client.session.get.side_effect = requests.HTTPError("unavailable")
        with self.assertRaises(CuratedTextUnavailable):
            self.fetch()
        self.client.session.get.side_effect = None
        self.response.iter_content.side_effect = lambda _: iter(
            [b"x" * (2 * 1024 * 1024), b"x"]
        )
        with self.assertRaises(CuratedTextUnavailable):
            self.fetch()
        self.response.close.assert_called()

    def test_logo_queries_or_malformed_presentation_are_omitted_without_blocking_media(
        self,
    ):
        for logo in (
            "https://images.example.invalid/search?q=public",
            "https://images.example.invalid/a?token=private-value",
            "https://user:private-value@images.example.invalid/a",
            "https://[broken/image",
        ):
            text = TEXT.replace("https://images.example.invalid/sample.png", logo)
            current = parse_source(text, load_sources()[0])[0]
            self.assertEqual(current.logo, "")
            self.assertEqual(current.stream_url, URL)
            self.assertTrue(is_public_media_transport(current))
            self.assertNotIn(
                "private-value", render_m3u([current], enforce_capabilities=True)
            )

    def test_corrupt_cache_and_persistence_errors_fail_closed_without_disclosing_values(
        self,
    ):
        self.fetch()
        path = self.cache / "curated_1.json"
        original = json.loads(path.read_text())
        for changed in (
            {**original, "version": 2},
            {**original, "observed_at": "2026-11-23T12:00:00"},
            {**original, "entries": {}},
            {**original, "entries": {"extra.sample": {}}},
        ):
            path.write_text(json.dumps(changed))
            with self.assertRaises(CuratedTextUnavailable):
                self.fetch(cache_only=True)
        path.unlink()
        with (
            patch(
                "legal_iptv.sources.curated_text.write_json_atomic",
                side_effect=OSError("private-value"),
            ),
            self.assertRaises(CuratedTextUnavailable) as stopped,
        ):
            self.fetch()
        self.assertNotIn("private-value", str(stopped.exception))

    def test_pruning_removes_only_owned_obsolete_snapshots(self):
        self.fetch()
        path = self.cache / "curated_1.json"
        obsolete = self.cache / "obsolete.json"
        obsolete.write_bytes(path.read_bytes())
        unrelated = self.cache / "notes.json"
        unrelated.write_text(json.dumps({"operator_note": "x" * (2 * 1024 * 1024)}))
        original_unrelated = (unrelated.read_bytes(), unrelated.stat().st_mtime_ns)
        self.fetch(cache_only=True)
        self.assertFalse(obsolete.exists())
        self.assertEqual(
            (unrelated.read_bytes(), unrelated.stat().st_mtime_ns), original_unrelated
        )

    def test_new_token_changes_exact_validation_context_without_inventing_expiry(self):
        first = self.fetch()[0]
        self.response.iter_content.side_effect = lambda _: iter(
            [TEXT.replace(TOKEN, "b" * 32).encode()]
        )
        second = fetch_channels(
            self.client, cache_dir=self.cache, now=NOW + timedelta(seconds=1801)
        )[0]
        self.assertNotEqual(
            context_for_channel(first).id, context_for_channel(second).id
        )
        self.assertTrue(context_for_channel(first).transient)
        self.assertIsNone(context_for_channel(first).expires_at)
        policy = ValidationPolicy()
        self.assertEqual(policy.interval(True, 4, transient=True), policy.base_seconds)

    def test_source_bounds_and_source_environment_are_validated_before_requests(self):
        for sources in (
            [SOURCE] * 9,
            [{**SOURCE, "id": "../bad"}],
            [{**SOURCE, "entries": [ENTRY] * 101}],
            [{**SOURCE, "url": "https://user:password@feed.example.invalid/list"}],
        ):
            self.sources = sources
            with self.assertRaises(CuratedTextUnavailable):
                self.fetch()
        self.client.session.get.assert_not_called()
        self.sources = [{**SOURCE, "url_env": "EXAMPLE_CURATED_URL"}]
        with patch.dict("os.environ", {"EXAMPLE_CURATED_URL": ""}):
            self.assertEqual(load_sources()[0].url, SOURCE["url"])
        with patch.dict(
            "os.environ",
            {"EXAMPLE_CURATED_URL": "https://other.example.invalid/feed.txt"},
        ):
            self.assertEqual(
                load_sources()[0].url, "https://other.example.invalid/feed.txt"
            )

    def test_media_rule_never_bypasses_flags_headers_or_exact_channel_url_origin(self):
        current = parse_source(TEXT, load_sources()[0])[0]
        cases = [
            {
                "stream_url": URL.replace(
                    "media.example.invalid", "evil.media.example.invalid"
                )
            },
            {"stream_url": URL + "&other=x"},
            {"stream_url": URL + "&token=" + TOKEN},
            {"stream_url": URL + "#fragment"},
            {"stream_url": URL.replace("?token=" + TOKEN, "")},
            {"stream_url": URL.replace("https://", "https://user:password@")},
            {"stream_url": URL.replace("/exact/", "/different/")},
            {"id": "extra.other"},
            {"source": "public_catalog"},
            {"source_type": "custom"},
            {"drm": {"type": "declared"}},
            {"request_headers": {"Referer": "https://public.example.invalid/"}},
            {"secret_refs": {"x": "PRIVATE"}},
            {"requires_dynamic_resolution": True},
            {"publishable_static": False},
            {"delivery_mode": "dai"},
        ]
        for change in cases:
            with self.subTest(change=change):
                candidate = replace(current, **change)
                self.assertFalse(is_public_media_transport(candidate))
                self.assertFalse(is_exportable(candidate, "portable"))
        self.assertTrue(is_public_media_transport(current))
        self.rules = []
        self.assertFalse(is_exportable(current, "portable"))

    def test_alphanumeric_token_rule_has_fixed_ascii_length(self):
        self.rules = [
            {
                **RULE,
                "query_parameters": {"token": {"format": "alphanumeric", "length": 32}},
            }
        ]
        for token in ("G" * 32, "aB09" * 8):
            current = parse_source(TEXT.replace(TOKEN, token), load_sources()[0])[0]
            self.assertTrue(is_public_media_transport(current))
        for token in ("a" * 31, "a" * 33, "_" * 32, "é" * 32):
            with self.assertRaises(ValueError):
                parse_source(TEXT.replace(TOKEN, token), load_sources()[0])

    def test_sensitive_signatures_cannot_be_whitelisted_as_public_parameters(self):
        current = parse_source(TEXT, load_sources()[0])[0]
        for name in ("sig", "signature", "SIG", "SIGNATURE"):
            self.rules = [
                {**RULE, "query_parameters": {name: {"format": "hex", "length": 32}}}
            ]
            candidate = replace(current, stream_url=URL.replace("token=", name + "="))
            self.assertFalse(is_public_media_transport(candidate))
            self.assertFalse(is_exportable(candidate, "portable"))

    def test_total_deadline_stops_slow_drip_closes_response_and_retains_exact_lkg(self):
        self.fetch()
        path = self.cache / "curated_1.json"
        original = (path.read_bytes(), path.stat().st_mtime_ns)
        self.response.close.reset_mock()
        self.response.iter_content.reset_mock()
        elapsed = [0.0]

        def drip(*_args, **_kwargs):
            elapsed[0] += 2.0
            return b"x"

        with (
            patch(
                "legal_iptv.sources.curated_text.time.monotonic",
                side_effect=lambda: elapsed[0],
            ),
            patch.object(self.response.raw, "read1", side_effect=drip) as reader,
        ):
            selected = fetch_channels(
                self.client, cache_dir=self.cache, now=NOW + timedelta(seconds=1801)
            )
        self.assertEqual(selected[0].stream_url, URL)
        self.assertEqual(reader.call_count, 10)
        self.response.iter_content.assert_not_called()
        self.response.close.assert_called_once()
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)
        self.assertEqual(
            self.client.session.get.call_args.kwargs["timeout"], (3.0, 3.0)
        )
        self.assertTrue(all(call.args[0] <= 4096 for call in reader.call_args_list))

    def test_raw_stream_timeout_uses_lkg_and_without_cache_raises_source_failure(self):
        self.fetch()
        path = self.cache / "curated_1.json"
        original = (path.read_bytes(), path.stat().st_mtime_ns)
        self.response.close.reset_mock()
        error = ReadTimeoutError(None, None, "private-value")
        with patch.object(self.response.raw, "read1", side_effect=error):
            selected = fetch_channels(
                self.client, cache_dir=self.cache, now=NOW + timedelta(seconds=1801)
            )
            self.assertEqual(selected[0].stream_url, URL)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)
            path.unlink()
            with self.assertRaises(CuratedTextUnavailable) as stopped:
                self.fetch()
        self.assertNotIn("private-value", str(stopped.exception))
        self.assertEqual(self.response.close.call_count, 2)

    def test_companion_main_only_does_not_change_main_transport_or_reference_behavior(
        self,
    ):
        current = parse_source(TEXT, load_sources()[0])[0]
        self.assertTrue(_transport_safe(current))
        self.assertIn(URL, render_m3u([current], enforce_capabilities=True))
        for identifier in (current.id, current.tvg_id, current.logical_channel_id):
            for preselected in (False, True):
                policy = {**load_policy(), "companion_excluded_ids": [identifier]}
                for names in ([], ["Sample"]):
                    selected, excluded = select_companion_channels(
                        [current],
                        {"base_reference_channels": names},
                        policy=policy,
                        preselected=preselected,
                    )
                    self.assertEqual(selected, [])
                    self.assertEqual(excluded[0]["reason"], "main_only")

    def test_aggregate_main_only_and_health_cache_keep_exact_sampled_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(
                make_config(directory, profile="full", validate_streams=True),
                require_media_proof=False,
                companion_output_path=root / "companion.m3u",
                companion_meta_output_path=root / "companion.meta.json",
                companion_guide_output_path=root / "companion-guide.xml",
                companion_reference_file=root / "reference.json",
                selection_proof_output_path=root / "selection-proof.json",
            )
            policy = {
                **load_policy(),
                "companion_excluded_ids": ["extra.sample"],
                "required_channel_ids": [],
            }
            (root / "reference.json").write_text(
                json.dumps(
                    {
                        "version": 3,
                        "generated_at": NOW.isoformat(),
                        "base_reference_channels": [f"Other {i}" for i in range(25)],
                        "directory_catalog_channels": [],
                    }
                )
            )
            with (
                patch(
                    "legal_iptv.services.aggregate.HttpClient", return_value=self.client
                ),
                patch(
                    "legal_iptv.sources.extra_channels.load_resource", return_value=[]
                ),
                patch(
                    "legal_iptv.services.aggregate.public_catalog.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.live_stream_catalog.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.load_xmltv_aliases", return_value={}
                ),
                patch("legal_iptv.services.companion.load_policy", return_value=policy),
                patch("legal_iptv.services.aggregate.load_policy", return_value=policy),
                patch(
                    "legal_iptv.services.link_validator.inspect_url",
                    return_value=UrlValidation(True, 200),
                ) as probe,
                patch("legal_iptv.services.link_validator._utc_now", return_value=NOW),
                patch("legal_iptv.sources.curated_text.datetime") as source_clock,
                patch("legal_iptv.services.selection_proof.datetime") as proof_clock,
            ):
                source_clock.now.return_value = NOW
                source_clock.fromisoformat.side_effect = datetime.fromisoformat
                proof_clock.now.return_value = NOW
                proof_clock.fromisoformat.side_effect = datetime.fromisoformat
                # A local empty guide allows unscheduled identities without fetching programming.
                config.guide_output_path.write_text("<tv/>")
                run_aggregation(config)
                self.assertEqual(probe.call_count, 1)
                self.assertIn(URL, config.output_path.read_text())
                self.assertTrue(
                    json.loads(config.meta_output_path.read_text())[
                        "media_proof_required"
                    ]
                )
                self.assertFalse(config.require_media_proof)
                self.assertNotIn(URL, config.companion_output_path.read_text())
                self.assertEqual(
                    json.loads(config.companion_meta_output_path.read_text())[
                        "excluded_by_reason"
                    ],
                    {"main_only": 1},
                )
                self.client.session.get.reset_mock()
                run_aggregation(replace(config, validation_due_only=True))
                self.client.session.get.assert_not_called()
                self.assertEqual(probe.call_count, 1)
                self.assertEqual(
                    json.loads(config.stream_status_file.read_text())["urls"][URL][
                        "checked_at"
                    ],
                    NOW.isoformat(),
                )
                verify_selection_proof(
                    config.output_path,
                    config.selection_proof_output_path,
                    config.stream_status_file,
                    companion=config.companion_output_path,
                    now=NOW,
                )
                self.assertEqual(
                    json.loads(config.selection_proof_output_path.read_text())[
                        "bindings"
                    ][0]["max_age_seconds"],
                    14400,
                )
                with self.assertRaises(ValueError):
                    verify_selection_proof(
                        config.output_path,
                        config.selection_proof_output_path,
                        config.stream_status_file,
                        now=NOW + timedelta(hours=4, seconds=1),
                    )

    def test_configured_source_failure_propagates_before_overwriting_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(directory, profile="full")
            config.output_path.write_text("previous publication")
            with (
                patch(
                    "legal_iptv.services.aggregate.HttpClient", return_value=self.client
                ),
                patch(
                    "legal_iptv.sources.extra_channels.load_resource", return_value=[]
                ),
                patch(
                    "legal_iptv.services.aggregate.public_catalog.fetch_channels",
                    return_value=[],
                ),
                patch(
                    "legal_iptv.services.aggregate.live_stream_catalog.fetch_channels",
                    return_value=[],
                ),
            ):
                self.client.session.get.side_effect = requests.HTTPError(
                    "private-value"
                )
                with self.assertRaises(CuratedTextUnavailable) as stopped:
                    run_aggregation(config)
            self.assertEqual(config.output_path.read_text(), "previous publication")
            self.assertNotIn("private-value", str(stopped.exception))

    def test_curated_always_needs_recent_media_even_when_global_proof_is_disabled(self):
        current = parse_source(TEXT, load_sources()[0])[0]
        ordinary = replace(
            current,
            id="ordinary",
            source_type=None,
            stream_url="https://media.example.invalid/plain.m3u8",
        )
        with tempfile.TemporaryDirectory() as directory:
            config = replace(make_config(directory), require_media_proof=False)
            self.assertEqual(
                _required_media_proof(config, [current, ordinary]), [ordinary]
            )
            record = success_record(current, checked_at=NOW.isoformat())
            config.stream_status_file.write_text(json.dumps({"urls": {URL: record}}))
            with patch("legal_iptv.services.link_validator._utc_now", return_value=NOW):
                self.assertEqual(
                    _required_media_proof(config, [current, ordinary]),
                    [current, ordinary],
                )
            with patch(
                "legal_iptv.services.link_validator._utc_now",
                return_value=NOW + timedelta(hours=4, seconds=1),
            ):
                self.assertEqual(
                    _required_media_proof(config, [current, ordinary]), [ordinary]
                )
