import io
import json
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from legal_iptv.exporters import render_m3u
from legal_iptv.services.selection_proof import main as proof_cli
from legal_iptv.services.selection_proof import (
    verify_selection_proof,
    write_selection_proof,
)
from legal_iptv.services.validation_schedule import context_for_channel
from tests.unit.test_media_proof import NOW, channel, success_record


class SelectionProofTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.main = self.root / "main.m3u"
        self.companion = self.root / "companion.m3u"
        self.status = self.root / "status.json"
        self.proof = self.root / "selection-proof.json"
        self.current = channel(
            tvg_id="Example.br",
            request_headers={"Referer": "https://example.test/player"},
        )

    def create(self, record=None, nested=False):
        record = record or success_record(self.current)
        if nested:
            record = {"contexts": {context_for_channel(self.current).id: record}}
        self.status.write_text(json.dumps({"urls": {self.current.stream_url: record}}))
        self.main.write_text(render_m3u([self.current]))
        self.companion.write_bytes(self.main.read_bytes())
        write_selection_proof([self.current], self.proof, self.status, now=NOW)

    def verify(self, **kwargs):
        verify_selection_proof(
            self.main,
            self.proof,
            self.status,
            companion=self.companion,
            now=NOW,
            **kwargs,
        )

    def test_roundtrip_flat_and_nested_status_bindings_without_raw_transports(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                self.create(nested=nested)
                self.verify()
                text = self.proof.read_text()
                self.assertNotIn(self.current.stream_url, text)
                self.assertNotIn("Referer", text)
                self.assertEqual(stat.S_IMODE(self.proof.stat().st_mode), 0o600)

    def test_changed_url_headers_identity_or_companion_fails(self):
        for change in (
            {"stream_url": "https://example.test/different.m3u8"},
            {"request_headers": {"Referer": "https://example.test/different"}},
            {"tvg_id": "Other.br"},
        ):
            with self.subTest(change=change):
                self.create()
                self.main.write_text(render_m3u([replace(self.current, **change)]))
                with self.assertRaises(ValueError):
                    self.verify()
        self.create()
        self.companion.write_text(
            render_m3u([replace(self.current, tvg_id="Other.br")])
        )
        with self.assertRaises(ValueError):
            self.verify()

    def test_status_context_legacy_future_and_second_failure_are_rejected(self):
        for changes in (
            {"context_id": "0" * 64},
            {"probe_version": 1},
            {"checked_at": (NOW + timedelta(seconds=1)).isoformat()},
            {
                "active": None,
                "status": "unknown",
                "last_media_success_at": NOW.isoformat(),
                "consecutive_failures": 2,
            },
        ):
            with self.subTest(changes=changes):
                self.create()
                record = success_record(self.current, **changes)
                self.status.write_text(
                    json.dumps({"urls": {self.current.stream_url: record}})
                )
                with self.assertRaises(ValueError):
                    self.verify()

    def test_single_inconclusive_retry_does_not_refresh_sample_time(self):
        record = success_record(
            self.current,
            active=None,
            status="unknown",
            consecutive_failures=1,
            last_media_success_at=(NOW - timedelta(hours=2)).isoformat(),
        )
        self.create(record)
        self.verify()
        self.assertEqual(
            json.loads(self.status.read_text())["urls"][self.current.stream_url], record
        )
        with self.assertRaises(ValueError):
            verify_selection_proof(
                self.main,
                self.proof,
                self.status,
                now=NOW + timedelta(days=1, seconds=1),
            )

    def test_expiry_outside_url_and_inferred_ttl_are_rechecked(self):
        for declarations in (
            {"expires_at": (NOW + timedelta(seconds=1800)).isoformat()},
            {"resolved_at": NOW.isoformat(), "ttl_seconds": 1800},
        ):
            with self.subTest(declarations=declarations):
                self.current = replace(
                    self.current,
                    **{
                        "expires_at": None,
                        "resolved_at": None,
                        "ttl_seconds": None,
                        **declarations,
                    },
                )
                self.create()
                self.verify()
                value = json.loads(self.proof.read_text())
                self.assertEqual(
                    value["bindings"][0]["expires_at"],
                    (NOW + timedelta(seconds=1800)).isoformat(),
                )
                with self.assertRaises(ValueError):
                    verify_selection_proof(
                        self.main,
                        self.proof,
                        self.status,
                        now=NOW + timedelta(seconds=901),
                    )
                verify_selection_proof(
                    self.main,
                    self.proof,
                    self.status,
                    now=NOW + timedelta(seconds=901),
                    min_ttl_seconds=800,
                )

    def test_malformed_playlists_and_unsigned_entries_fail_closed(self):
        for payload in (
            "invalid",
            '#EXTM3U\n#EXTINF:-1 tvg-id="Example.br",Example\n',
            "#EXTM3U\n#EXTINF:-1,Example\nhttps://example.test/a\n",
            "#EXTM3U\nhttps://example.test/a\n",
            '#EXTM3U\n#EXTINF:-1 tvg-id="Example.br",Example\n#EXTINF:-1 tvg-id="Other.br",Other\n',
        ):
            self.create()
            self.main.write_text(payload)
            with self.assertRaises(ValueError):
                self.verify()

    def test_writer_rejects_invalid_policy_missing_evidence_and_missing_identity(self):
        for kwargs in ({"min_ttl_seconds": -1}, {"max_age_seconds": 86401}):
            with self.assertRaises(ValueError):
                write_selection_proof([], self.proof, self.status, now=NOW, **kwargs)
        with self.assertRaises(ValueError):
            write_selection_proof([self.current], self.proof, self.status, now=NOW)
        self.current = replace(self.current, tvg_id=None)
        self.status.write_text(
            json.dumps(
                {"urls": {self.current.stream_url: success_record(self.current)}}
            )
        )
        with self.assertRaises(ValueError):
            write_selection_proof([self.current], self.proof, self.status, now=NOW)

    def test_cli_verifies_offline_and_hides_failure_inputs(self):
        self.create()
        args = [
            "proof",
            "--playlist",
            str(self.main),
            "--proof",
            str(self.proof),
            "--status",
            str(self.status),
            "--companion",
            str(self.companion),
            "--min-ttl",
            "900",
        ]
        with (
            patch.object(sys, "argv", args),
            patch("legal_iptv.services.selection_proof.datetime") as clock,
        ):
            clock.now.return_value = NOW
            clock.fromisoformat.side_effect = lambda value: __import__(
                "datetime"
            ).datetime.fromisoformat(value)
            proof_cli()
            self.main.write_text("invalid")
            with (
                patch("sys.stderr", new_callable=io.StringIO) as error,
                self.assertRaises(SystemExit) as stopped,
            ):
                proof_cli()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(error.getvalue(), "Selection proof verification failed\n")

    def test_invalid_schema_limit_duplicate_and_missing_bindings_fail(self):
        for changes in (
            {"schema_version": True},
            {"max_age_seconds": 86401},
            {"bindings": []},
        ):
            self.create()
            value = json.loads(self.proof.read_text())
            value.update(changes)
            self.proof.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                self.verify()
        self.create()
        value = json.loads(self.proof.read_text())
        value["bindings"] *= 2
        self.proof.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.verify()
