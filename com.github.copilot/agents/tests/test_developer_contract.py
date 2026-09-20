#!/usr/bin/env python3
"""Pin the developer agent's approved-review delivery contract."""

from __future__ import annotations

import unittest
from pathlib import Path
import re

AGENT = Path(__file__).resolve().parents[1] / "developer.agent.md"


class DeliveryContractTest(unittest.TestCase):
    """Approval must authorize an exact, verified stage/commit/push set."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = AGENT.read_text(encoding="utf-8")

    def test_close_report_is_explicit_approval(self) -> None:
        self.assertIn("copilot --agent agents:developer", self.text)
        self.assertIn(
            "An `approved` tuicr close report is explicit human approval",
            self.text,
        )

    def test_only_reported_paths_are_staged(self) -> None:
        self.assertIn("stage exactly the paths in `marks.files`", self.text)
        self.assertIn("Never use `git add -A`, `git add .`", self.text)
        self.assertRegex(
            self.text,
            re.compile(r"Unrelated\s+worktree changes remain unstaged"),
        )

    def test_stale_or_unattributed_work_is_refused(self) -> None:
        self.assertRegex(self.text, re.compile(r"any change\s+since the report"))
        self.assertIn("any staged path has no author row", self.text)

    def test_push_uses_only_configured_upstream(self) -> None:
        self.assertIn("push to the branch's configured upstream", self.text)
        self.assertIn("force-push", self.text)
        self.assertIn("without undoing the local commit", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
