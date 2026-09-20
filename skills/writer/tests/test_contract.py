#!/usr/bin/env python3
"""Pin writer's semantic safety contract."""

from __future__ import annotations

import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class ContractTest(unittest.TestCase):
    """Guard the behavior that character-count tests cannot prove."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = SKILL.read_text(encoding="utf-8")

    def test_auto_invokes_for_human_documentation(self) -> None:
        self.assertIn("AUTO-INVOKE whenever drafting or revising documentation", self.text)
        self.assertIn("Use when writing or editing documentation a human will review", self.text)

    def test_preserves_semantics_and_obligations(self) -> None:
        self.assertIn("Claims, requirements, decisions, warnings", self.text)
        self.assertIn("no changed claim, softened requirement, lost", self.text)

    def test_removes_nonessential_divergence(self) -> None:
        self.assertIn("Historical context that does not explain current behavior", self.text)
        self.assertIn("Side topics that break the document's central theme", self.text)

    def test_asks_before_uncertain_omission(self) -> None:
        self.assertIn("ask whether", self.text.lower())
        self.assertIn("Never silently resolve uncertain scope", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
