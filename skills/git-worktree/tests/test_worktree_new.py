#!/usr/bin/env python3
"""Tests for home-staged worktree path construction."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "worktree_new.py"
SPEC = importlib.util.spec_from_file_location("worktree_new", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class StagedWorktreePathTests(unittest.TestCase):
    def test_preserves_branch_hierarchy_under_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            actual = MODULE.staged_worktree_path(Path("/src/widgets"), "feat/auth", home)
            self.assertEqual(actual, home / ".worktrees" / "widgets" / "feat" / "auth")

    def test_rejects_path_traversal(self) -> None:
        with self.assertRaises(MODULE.UsageError):
            MODULE.staged_worktree_path(Path("/src/widgets"), "feat/../escape")


if __name__ == "__main__":
    unittest.main()
