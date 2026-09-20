#!/usr/bin/env python3
"""Tests for writer's deterministic comparison helper."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "lib" / "compare.py"
spec = importlib.util.spec_from_file_location("writer_compare", MODULE)
writer_compare = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(writer_compare)


class CompareTest(unittest.TestCase):
    """Cover reduction and explicit retention checks."""

    def test_shorter_result_passes(self) -> None:
        metrics = writer_compare.compare("A repeated repeated point.", "A point.", [])
        self.assertTrue(metrics["shorter"])
        self.assertEqual(metrics["missing_required"], [])
        self.assertGreater(metrics["reduction_percent"], 0)

    def test_longer_result_fails_reduction(self) -> None:
        metrics = writer_compare.compare("Brief.", "Needlessly expanded.", [])
        self.assertFalse(metrics["shorter"])

    def test_missing_required_text_is_reported(self) -> None:
        metrics = writer_compare.compare(
            "Deploy only after approval.", "Deploy later.", ["after approval"]
        )
        self.assertEqual(metrics["missing_required"], ["after approval"])

    def test_unicode_counts_as_characters(self) -> None:
        metrics = writer_compare.compare("agent — concise", "agent — clear", [])
        self.assertEqual(metrics["source_chars"], 15)
        self.assertEqual(metrics["result_chars"], 13)


if __name__ == "__main__":
    unittest.main(verbosity=2)
