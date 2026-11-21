import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legal_iptv.io import write_json_atomic, write_text_atomic


class AtomicStorageTest(unittest.TestCase):
    def test_serialization_failure_preserves_destination_and_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "cache.json"
            target.write_text('{"old":true}')
            with self.assertRaises(TypeError):
                write_json_atomic(target, {"unsupported": object()})
            self.assertEqual(target.read_text(), '{"old":true}')
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_replace_failure_cleans_temporary_and_keeps_old_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "playlist.m3u"
            target.write_text("old")
            with patch.object(Path, "replace", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    write_text_atomic(target, "new")
            self.assertEqual(target.read_text(), "old")
            self.assertEqual(list(Path(directory).iterdir()), [target])
