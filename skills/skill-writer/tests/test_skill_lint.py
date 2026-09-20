#!/usr/bin/env python3
"""Regression tests for the constrained frontmatter parser."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "lib" / "skill-lint.py"
spec = importlib.util.spec_from_file_location("skill_lint", MODULE)
skill_lint = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(skill_lint)


class ScalarParsingTest(unittest.TestCase):
    """Double-quoted YAML scalars preserve Unicode while decoding escapes."""

    def test_unicode_is_not_mojibake(self) -> None:
        self.assertEqual(skill_lint._parse_scalar('"agent — skill"'), "agent — skill")

    def test_json_escapes_are_decoded(self) -> None:
        self.assertEqual(skill_lint._parse_scalar('"line\\nquote: \\"ok\\""'), 'line\nquote: "ok"')

    def test_invalid_escape_is_rejected(self) -> None:
        with self.assertRaises(skill_lint.FrontmatterError):
            skill_lint._parse_scalar('"bad\\qescape"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
