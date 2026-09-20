#!/usr/bin/env python3
"""Tests for pr_render.py — template resolution and ticket-link placement."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

from pr_render import render_body, resolve_template  # noqa: E402
from profile_resolve import PR_FALLBACK, load_profile  # noqa: E402

URL = "https://tracker.test/browse/ABC-9"

PROFILE = """
name = "unit"
[branch]
pattern = '^(?P<ticket>[A-Z]+-[0-9]+)-(?P<slug>[a-z-]+)$'
[ticket]
url = "https://tracker.test/browse/{ticket}"
[pr]
templates = ["first/template.md", "second/template.md"]
"""


def make_repo(tmp: str) -> Path:
    """Init a git repo carrying the unit profile."""
    repo = Path(tmp)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (repo / ".dev.toml").write_text(PROFILE, encoding="utf-8")
    return repo


def write(path: Path, text: str) -> Path:
    """Write *text*, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the entry point as a subprocess."""
    return subprocess.run(
        [sys.executable, str(LIB / "pr_render.py"), *args],
        capture_output=True, text=True,
    )


class TestTemplateResolution(unittest.TestCase):
    """Candidates are tried in profile order, then the bundled fallback."""

    def test_first_candidate_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            write(repo / "first" / "template.md", "# A\n")
            write(repo / "second" / "template.md", "# B\n")
            profile = load_profile(repo)
            self.assertEqual(
                resolve_template(repo, profile), repo / "first" / "template.md"
            )

    def test_second_used_when_first_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            write(repo / "second" / "template.md", "# B\n")
            self.assertEqual(
                resolve_template(repo, load_profile(repo)),
                repo / "second" / "template.md",
            )

    def test_falls_back_to_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            self.assertEqual(resolve_template(repo, load_profile(repo)), PR_FALLBACK)


class TestTicketPlacement(unittest.TestCase):
    """Placement is positional, so it works for any tracker's wording."""

    def test_explicit_placeholder(self) -> None:
        body = render_body("# T\n\n{TICKET_URL}\n", URL, "")
        self.assertIn(URL, body)
        self.assertNotIn("{TICKET_URL}", body)

    def test_inserted_under_first_heading(self) -> None:
        body = render_body("# Related issue\n_Link it_\n\n# Description\n", URL, "")
        lines = [line for line in body.splitlines() if line.strip()]
        self.assertEqual(lines[0], "# Related issue")
        self.assertEqual(lines[1], URL)

    def test_italic_placeholder_dropped(self) -> None:
        body = render_body("# Ticket\n_Link the ticket_\n", URL, "")
        self.assertNotIn("_Link the ticket_", body)

    def test_prepended_when_no_heading(self) -> None:
        body = render_body("just prose\n", URL, "")
        self.assertTrue(body.startswith(URL))
        self.assertIn("just prose", body)

    def test_only_first_heading_receives_url(self) -> None:
        body = render_body("# One\n\n# Two\n", URL, "")
        self.assertEqual(body.count(URL), 1)


class TestDescription(unittest.TestCase):
    """The description fills its section, or is appended."""

    def test_fills_description_section(self) -> None:
        body = render_body(
            "# Ticket\n_link_\n\n# Description\n_what changed_\n", URL, "It changed.\n"
        )
        self.assertIn("It changed.", body)
        self.assertNotIn("_what changed_", body)

    def test_appended_when_no_section(self) -> None:
        body = render_body("# Ticket\n", URL, "It changed.\n")
        self.assertTrue(body.rstrip().endswith("It changed."))

    def test_placeholder_form(self) -> None:
        body = render_body("# T\n{TICKET_URL}\n{DESCRIPTION}\n", URL, "Body text.")
        self.assertIn("Body text.", body)
        self.assertNotIn("{DESCRIPTION}", body)


class TestCli(unittest.TestCase):
    """End-to-end behaviour, including the exclude entry."""

    def test_writes_pr_and_excludes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            for _ in range(2):
                result = run_cli(
                    "--worktree", str(repo), "--ticket-url", URL, "--force",
                    "--title", "Add widget",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
            self.assertEqual(
                [line for line in exclude.splitlines() if line.strip() == "PR.md"],
                ["PR.md"],
            )
            self.assertIn("<!-- PR title: Add widget -->", (repo / "PR.md").read_text())

    def test_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            self.assertEqual(
                run_cli("--worktree", str(repo), "--ticket-url", URL).returncode, 0
            )
            self.assertEqual(
                run_cli("--worktree", str(repo), "--ticket-url", URL).returncode, 4
            )

    def test_no_exclude_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            run_cli("--worktree", str(repo), "--ticket-url", URL, "--no-exclude")
            exclude = repo / ".git" / "info" / "exclude"
            text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            self.assertNotIn("PR.md", text)

    def test_missing_worktree_exits_3(self) -> None:
        self.assertEqual(
            run_cli("--worktree", "/nonexistent/xyz", "--ticket-url", URL).returncode, 3
        )

    def test_requires_ticket_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_cli("--worktree", tmp).returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
