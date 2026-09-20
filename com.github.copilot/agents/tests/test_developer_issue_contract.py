#!/usr/bin/env python3
"""Pin the developer agent's composed GitHub-issue-filing contract.

The developer agent composes the ``github-issue`` skill with ``writer``,
``tuicr`` and the GitHub MCP server rather than filing issues itself. This
pins the composition and gating rules that only the orchestrating agent can
own: issue creation is blocked until the exact draft markdown is human-marked
reviewed, authorship uses concrete model IDs and the human's real
``git config user.email``, the local review repo/session persists, a wake
event alone never creates an issue, and only an approved close report
authorizes the call.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "developer.agent.md"


class DeveloperIssueContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = AGENT.read_text(encoding="utf-8")

    def test_composes_github_issue_writer_tuicr_and_mcp(self) -> None:
        for token in ("github-issue", "writer", "tuicr"):
            self.assertIn(token, self.text, f"expected the developer agent to name the {token} skill")
        self.assertRegex(self.text, re.compile(r"MCP", re.IGNORECASE))

    def test_issue_creation_is_blocked_until_human_marked_reviewed(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(
                r"issue creation[^.\n]{0,80}blocked[^.\n]{0,80}human[- ]marked[^.\n]{0,20}reviewed",
                re.IGNORECASE,
            ),
        )
        self.assertRegex(self.text, re.compile(r"exact\s+[Mm]arkdown", re.IGNORECASE))

    def test_uses_git_config_user_email_for_human_identity(self) -> None:
        self.assertIn("git config", self.text)
        self.assertRegex(self.text, re.compile(r"user\.email", re.IGNORECASE))

    def test_uses_concrete_model_ids_never_placeholders(self) -> None:
        self.assertRegex(self.text, re.compile(r"concrete model ID", re.IGNORECASE))

    def test_preserves_the_local_review_repo_and_session(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"(preserve|persist)[^.\n]{0,40}(local )?review (repo|session)", re.IGNORECASE),
        )

    def test_never_creates_an_issue_from_a_wake_event(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"never\s+create\s+an?\s+issue\s+from\s+a\s+wake\s+event", re.IGNORECASE),
        )

    def test_acts_only_on_an_approved_close_report(self) -> None:
        self.assertRegex(
            self.text,
            re.compile(r"only\s+(?:act|create|proceed)[^.\n]{0,60}approved\s+close\s+report", re.IGNORECASE),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
