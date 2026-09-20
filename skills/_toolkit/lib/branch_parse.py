#!/usr/bin/env python3
"""Validate and parse a feature branch name against the platform profile.

Everything downstream keys off the parsed branch — the ticket id names the tmux
session and the review scope, the slug names the worktree, the ticket URL heads
the PR body — so the parse happens once, up front, and fails loudly.

Unlike a hard-coded parser, the accepted shape comes entirely from the
profile's ``[branch].pattern``, and the rejection message quotes
``[branch].describe`` so the human sees their own convention rather than a
regex. The emitted keys are vendor-neutral (``TICKET_ID``, not ``JIRA_ID``).

Emits ``KEY=VALUE``: ``BRANCH``, ``PREFIX``, ``TICKET_ID``, ``TICKET_URL``,
``SLUG``, ``SCOPE``, ``WORKTREE_SUFFIX``.

``SCOPE`` is the compact label used for tmux windows, watcher names and
``review_cycles.scope``; it is the ticket id.

Exit codes:
    2   Usage error (no branch given).
    3   No profile found.
    4   Branch does not match the profile's pattern.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_resolve import Profile, load_profile  # noqa: E402
from skillkit.cli import emit_all, run_main  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402


def _apply_case(ticket: str, mode: str) -> str:
    """Normalise the captured ticket id per ``[branch].ticket_case``."""
    if mode == "upper":
        return ticket.upper()
    if mode == "lower":
        return ticket.lower()
    return ticket


def parse_branch(branch: str, profile: Profile) -> dict[str, str]:
    """Parse *branch* against *profile* and return the emitted fields.

    Args:
        branch: Full branch name.
        profile: Validated platform profile.

    Returns:
        dict[str, str]: BRANCH, PREFIX, TICKET_ID, TICKET_URL, SLUG, SCOPE,
        WORKTREE_SUFFIX.

    Raises:
        SkillError: Code 4 when the branch does not match the profile pattern.
    """
    match = profile.pattern.fullmatch(branch)
    if not match:
        raise SkillError(
            f"branch '{branch}' does not match profile '{profile.name}'. "
            f"Expected: {profile.describe}",
            code=4,
        )

    groups = match.groupdict()
    ticket = _apply_case(groups["ticket"], profile.ticket_case)

    return {
        "BRANCH": branch,
        "PREFIX": groups.get("prefix") or "",
        "TICKET_ID": ticket,
        "TICKET_URL": profile.ticket_url.replace("{ticket}", ticket),
        "SLUG": groups["slug"],
        "SCOPE": ticket,
        "WORKTREE_SUFFIX": branch.replace("/", "-"),
    }


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="branch_parse.py",
        description="Parse a feature branch name using the platform profile.",
    )
    parser.add_argument("branch", nargs="?", help="Full branch name.")
    parser.add_argument("--repo", default="", help="Repo root searched for .dev.toml.")
    parser.add_argument("--profile", default="", help="Bundled profile name or path.")
    known, extra = parser.parse_known_args(argv)
    if extra:
        raise UsageError(f"unknown arg '{extra[0]}'")
    if not known.branch:
        raise UsageError("a branch name is required")

    profile = load_profile(known.repo or None, known.profile)
    emit_all(parse_branch(known.branch, profile))
    return 0


if __name__ == "__main__":
    run_main(main)
