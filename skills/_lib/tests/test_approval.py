#!/usr/bin/env python3
"""Provider-neutral approval receipt contract tests."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skillkit.approval import ApprovalError, ApprovalReceipt, ReviewedFile


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "input.json").write_bytes(b"reviewed input")
        self.receipt = ApprovalReceipt(
            str(self.root), "abc", True,
            (ReviewedFile("input.json", hashlib.sha256(b"reviewed input").hexdigest()),),
        )

    def read(self, value):
        receipt = ApprovalReceipt.from_dict(value)
        with patch("skillkit.approval.git", return_value=SimpleNamespace(ok=True, stdout="abc")):
            return receipt.read_inputs(self.root, "abc", ("input.json",))

    def test_any_provider_can_emit_the_generic_contract(self):
        self.assertEqual(self.read(self.receipt.to_dict()), {"input.json": b"reviewed input"})

    def test_unknown_schema_and_denied_approval_are_rejected(self):
        for update in ({"schema": "other/v2"}, {"approved": False}):
            with self.subTest(update=update), self.assertRaises(ApprovalError):
                self.read({**self.receipt.to_dict(), **update})

    def test_changed_input_is_rejected(self):
        (self.root / "input.json").write_bytes(b"changed")
        with self.assertRaises(ApprovalError):
            self.read(self.receipt.to_dict())

    def test_duplicate_absolute_and_traversal_paths_are_rejected(self):
        original = self.receipt.to_dict()["files"][0]
        for files in (
            [original, original],
            [{**original, "path": "../input.json"}],
            [{**original, "path": "/input.json"}],
        ):
            with self.subTest(files=files), self.assertRaises(ApprovalError):
                self.read({**self.receipt.to_dict(), "files": files})

    def test_missing_files_and_changed_head_are_rejected(self):
        for update in ({"files": []}, {"head": "different"}):
            with self.subTest(update=update), self.assertRaises(ApprovalError):
                self.read({**self.receipt.to_dict(), **update})


if __name__ == "__main__":
    unittest.main(verbosity=2)
