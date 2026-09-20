#!/usr/bin/env python3
"""Pin the human-owned persisted-session contract."""

from __future__ import annotations

import unittest
from pathlib import Path
import re

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class SessionContractTest(unittest.TestCase):
    """Agents preserve marks by reusing tuicr's persisted session."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SKILL.read_text(encoding="utf-8")

    def test_reuses_persisted_session(self) -> None:
        self.assertIn("Reuse the persisted session", self.text)
        self.assertIn("unchanged files retain their marks", self.text)

    def test_tuicr_owns_invalidation(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"tuicr\s+automatically clears marks for changed files"),
        )

    def test_session_files_are_not_manipulated(self) -> None:
        self.assertIn(
            "Never move, replace, delete, or edit a persisted session file",
            self.text,
        )
        self.assertIn("human explicitly requests", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
