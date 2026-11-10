#!/usr/bin/env python3
"""Regression tests for the constrained frontmatter parser."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
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


class SkillDocumentationTest(unittest.TestCase):
    def test_skill_without_readme_passes_strict_lint(self):
        with tempfile.TemporaryDirectory() as root:
            skill = Path(root) / "example"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                '---\nname: example\ndescription: "Use when checking an example."\n---\n'
                '# Example\n\nA standalone capability.\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(MODULE), str(skill), "--strict"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_script_paths_must_be_skill_relative_and_defined(self):
        cases = {
            "Run `~/.agents/skills/example/lib/run.py`.": "hardcoded",
            "Run `$SKILL_DIR/lib/run.py`.": "without defining",
            "`$SKILL_DIR` is the directory containing this SKILL.md. "
            "Run `$SKILL_DIR/lib/run.py`.": None,
        }
        for body, error in cases.items():
            rep = skill_lint.Report("example")
            skill_lint.check_script_paths(body, rep)
            if error is None:
                self.assertEqual(rep.errors, [], body)
            else:
                self.assertTrue(any(error in item for item in rep.errors), body)

    def test_scaffolder_does_not_generate_duplicate_readme(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run(
                [sys.executable, str(MODULE.with_name("skill_new.py")),
                 "example", "--root", root],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                {path.name for path in (Path(root) / "example").iterdir()},
                {"SKILL.md", "lib"},
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
