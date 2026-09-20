#!/usr/bin/env python3
"""Pin the ``github-issue`` skill's behavioral contract in ``SKILL.md``.

Prose-level checks for the properties that cannot be expressed as executable
assertions against a library: how the skill resolves its target, what it must
inspect and search before drafting, what it must validate, and how it must
speak to the human and to MCP. The persistence, draft-format and publication
gate contracts are pinned separately as executable unit tests against
``lib/issue_workspace.py``, ``lib/issue_draft.py`` and ``lib/issue_review.py``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class SkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.exists = SKILL.exists()
        cls.text = SKILL.read_text(encoding="utf-8") if cls.exists else ""

    def test_skill_md_exists(self) -> None:
        self.assertTrue(self.exists, f"expected {SKILL} to exist")

    def test_resolves_explicit_repo_or_current_checkout_remote(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"explicit(?:ly)?[- ]?(?:named|stated|given)? ?repo", re.IGNORECASE),
        )
        self.assertRegex(self.text, re.compile(r"current checkout'?s? remote", re.IGNORECASE))

    def test_asks_on_ambiguous_target(self) -> None:
        self.assertRegex(self.text, re.compile(r"ambigu", re.IGNORECASE))
        self.assertRegex(self.text, re.compile(r"\bask\b", re.IGNORECASE))

    def test_inspects_issue_templates_and_contribution_guidance(self) -> None:
        self.assertRegex(self.text, re.compile(r"issue template", re.IGNORECASE))
        self.assertRegex(
            self.text,
            re.compile(r"contribut(?:ing|ion) guid", re.IGNORECASE),
        )

    def test_duplicate_search_is_mandatory(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"(mandatory|must)[^.\n]{0,40}duplicate search", re.IGNORECASE),
        )

    def test_draft_sections_are_adaptive_not_fixed(self) -> None:
        self.assertRegex(self.text, re.compile(r"adaptive", re.IGNORECASE))

    def test_validates_requested_or_implied_metadata(self) -> None:
        self.assertRegex(self.text, re.compile(r"clearly implied", re.IGNORECASE))
        self.assertRegex(self.text, re.compile(r"validat", re.IGNORECASE))

    def test_uses_concise_prose_without_duplicating_writer(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"(?:without|not) duplicat\w* (?:the )?writer", re.IGNORECASE),
        )

    def test_uses_official_mcp_issue_tools(self) -> None:
        # The official GitHub MCP server exposes `search_issues`, not a
        # nonexistent `issue_search` -- naming the wrong tool would make the
        # duplicate-search step fail against the real server.
        for tool in ("search_issues", "issue_read", "issue_write"):
            self.assertIn(
                tool,
                self.text,
                f"expected the official MCP tool '{tool}' to be named",
            )
        self.assertNotRegex(
            self.text,
            re.compile(r"issue[_ ]search", re.IGNORECASE),
            "issue_search is not a real GitHub MCP tool; use search_issues",
        )

    def test_auth_failures_are_explicit(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"auth\w*\s+failure", re.IGNORECASE),
        )
        self.assertRegex(self.text, re.compile(r"explicit", re.IGNORECASE))

    def test_never_invents_identity(self) -> None:
        self.assertRegex(self.text, re.compile(r"never invent", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main(verbosity=2)
