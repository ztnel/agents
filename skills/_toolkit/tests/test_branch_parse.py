#!/usr/bin/env python3
"""Tests for branch_parse.py — profile-driven parsing across conventions."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
sys.path.insert(0, str(LIB))

from branch_parse import parse_branch  # noqa: E402
from profile_resolve import load_profile  # noqa: E402
from skillkit.cli import parse_metadata  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402

#: No prefix group at all — the pattern need only capture ticket and slug.
NO_PREFIX = """
name = "no-prefix"
[branch]
pattern = '^(?P<ticket>[A-Z]+-[0-9]+)-(?P<slug>[a-z0-9-]+)$'
describe = "<KEY-123>-<kebab-title>"
[ticket]
url = "https://t/{ticket}"
"""

LOWERCASE = """
name = "lower"
[branch]
pattern = '^(?P<ticket>[A-Za-z]+-[0-9]+)-(?P<slug>[a-z0-9-]+)$'
ticket_case = "lower"
[ticket]
url = "https://t/{ticket}"
"""

PRESERVE = LOWERCASE.replace('ticket_case = "lower"', 'ticket_case = "preserve"')


def profile_from(text: str, tmp: str):
    """Materialise an inline profile and load it."""
    path = Path(tmp) / "p.toml"
    path.write_text(text, encoding="utf-8")
    return load_profile(None, str(path))


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the entry point as a subprocess to assert exit codes."""
    return subprocess.run(
        [sys.executable, str(LIB / "branch_parse.py"), *args],
        capture_output=True, text=True,
    )


class TestAcrossConventions(unittest.TestCase):
    """The same parser must handle unrelated ticket grammars."""

    def test_letter_key_ticket(self) -> None:
        fields = parse_branch(
            "feature/abc-75-fix-the-widget", load_profile(None, "example-jira-ado")
        )
        self.assertEqual(fields["TICKET_ID"], "ABC-75")
        self.assertEqual(fields["PREFIX"], "feature")
        self.assertEqual(fields["SLUG"], "fix-the-widget")
        self.assertEqual(fields["TICKET_URL"], "https://example.atlassian.net/browse/ABC-75")

    def test_bare_numeric_ticket(self) -> None:
        """A forge using bare issue numbers needs no code change."""
        fields = parse_branch(
            "feat/123-add-widget", load_profile(None, "example-github")
        )
        self.assertEqual(fields["TICKET_ID"], "123")
        self.assertEqual(fields["TICKET_URL"], "https://github.com/OWNER/REPO/issues/123")

    def test_personal_prefix(self) -> None:
        fields = parse_branch(
            "@alice/abc-9-tidy-up", load_profile(None, "example-jira-ado")
        )
        self.assertEqual(fields["PREFIX"], "@alice")
        self.assertEqual(fields["TICKET_ID"], "ABC-9")

    def test_profile_without_prefix_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fields = parse_branch("ABC-5-do-thing", profile_from(NO_PREFIX, tmp))
            self.assertEqual(fields["PREFIX"], "")
            self.assertEqual(fields["TICKET_ID"], "ABC-5")


class TestTicketCase(unittest.TestCase):
    """ticket_case normalises the captured id."""

    def test_lower(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fields = parse_branch("ABC-5-do-thing", profile_from(LOWERCASE, tmp))
            self.assertEqual(fields["TICKET_ID"], "abc-5")

    def test_preserve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fields = parse_branch("AbC-5-do-thing", profile_from(PRESERVE, tmp))
            self.assertEqual(fields["TICKET_ID"], "AbC-5")


class TestRejection(unittest.TestCase):
    """A rejection must teach the human their own convention."""

    def test_quotes_describe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = profile_from(NO_PREFIX, tmp)
            with self.assertRaises(SkillError) as ctx:
                parse_branch("garbage", profile)
            self.assertEqual(ctx.exception.code, 4)
            self.assertIn("<KEY-123>-<kebab-title>", str(ctx.exception))

    def test_partial_match_is_rejected(self) -> None:
        """fullmatch, not search: trailing junk must not be accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            profile = profile_from(NO_PREFIX, tmp)
            with self.assertRaises(SkillError):
                parse_branch("ABC-5-do-thing/extra", profile)

    def test_cli_exit_4(self) -> None:
        result = run_cli("nonsense", "--profile", "example-github")
        self.assertEqual(result.returncode, 4)
        self.assertIn("does not match profile", result.stderr)

    def test_missing_branch_is_usage_error(self) -> None:
        self.assertEqual(run_cli("--profile", "default").returncode, 2)


class TestEmittedContract(unittest.TestCase):
    """The KEY=VALUE block is eval-ed by callers, so its shape is API."""

    def test_keys_are_vendor_neutral(self) -> None:
        result = run_cli("feat/123-add-widget", "--profile", "example-github")
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = parse_metadata(result.stdout)
        self.assertEqual(
            set(meta),
            {"BRANCH", "PREFIX", "TICKET_ID", "TICKET_URL", "SLUG", "SCOPE",
             "WORKTREE_SUFFIX"},
        )

    def test_scope_is_ticket_and_suffix_is_path_safe(self) -> None:
        result = run_cli("feat/123-add-widget", "--profile", "example-github")
        meta = parse_metadata(result.stdout)
        self.assertEqual(meta["SCOPE"], meta["TICKET_ID"])
        self.assertNotIn("/", meta["WORKTREE_SUFFIX"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
