#!/usr/bin/env python3
"""git-commit skill — commit staged changes with a provenance/approval trailer.

Trailer block produced::

    <description>

    Co-authored-by: Copilot (<author-model-1>) <...>, Copilot (<author-model-2>) <...>
       (a single comma-separated line, one entry per distinct author)
    Approved-by: <name> <email>
    Reviewed-by: <name> <email>, Copilot (<reviewer-1>) <...>, Copilot (<reviewer-2>) <...>

Constraint: the human-review gate is mandatory and is never
bypassed — the commit is refused without ``--confirm-reviewed``. This script
never stages anything itself and never pushes.

Ported from commit.py. The shell version used ``declare -A`` for author
dedupication, which is bash-4-only and therefore broken on a stock macOS
(bash 3.2.57); ``dict.fromkeys`` both dedupes and preserves order.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))

from skillkit.cli import run_main  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402
from skillkit.proc import run  # noqa: E402

#: Noreply address every Copilot trailer entry is attributed to.
COPILOT_EMAIL = "223556219+Copilot@users.noreply.github.com"

#: Errors keep the shell version's `commit.py: error: ...` prefix and exit 1,
#: because that string is what a caller greps for.
_ERROR_PREFIX = "commit.py: error:"


class CommitError(SkillError):
    """A refusal, rendered in the shell script's error format."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{_ERROR_PREFIX} {message}", code=1, raw=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="commit.py",
        usage='commit.py --description "<one sentence>" [options]',
        description=(
            "Commits the changes the human has already staged (git add) with a standardized\n"
            "provenance/approval trailer block. Never stages anything itself; if nothing is\n"
            "staged, nothing is committed. Never pushes."
        ),
        epilog=(
            "The human approver is always the first entry on the Reviewed-by line (explicit\n"
            "review + approval), so review is always recorded even with no agent reviewers."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("--description", default="", help="Commit subject. Keep it brief — ideally one sentence.")
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="Asserts the human has reviewed the staged changes. Set this ONLY after "
        "explicit human confirmation.",
    )
    parser.add_argument(
        "--author-model",
        action="append",
        default=[],
        metavar="<id>",
        help="Model ID of an agent that generated part of the staged code. REPEATABLE — each "
        "distinct model adds an entry to the single comma-separated Co-authored-by line. "
        "Defaults to $COPILOT_MODEL if none given.",
    )
    parser.add_argument(
        "--reviewer-model",
        action="append",
        default=[],
        metavar="<id>",
        help="Model ID of an agent reviewer. Repeatable; each adds an entry to the single "
        "comma-separated Reviewed-by line, after the human approver.",
    )
    parser.add_argument(
        "--approved-by",
        default="",
        metavar='"<name> <email>"',
        help="Override the approver identity. Defaults to local git config user.name/user.email.",
    )
    parser.add_argument("-h", "--help", action="help", help="Show this help.")

    known, extra = parser.parse_known_args(argv)
    if extra:
        raise CommitError(f"unknown argument: {extra[0]} (use -h for usage)")
    return known


def resolve_approver(override: str) -> str:
    """The approver identity as ``Name <email>``.

    Args:
        override: Explicit ``--approved-by`` value, or empty to read git config.

    Raises:
        CommitError: If neither an override nor a complete git identity exists.
    """
    if override:
        return override
    name = run(["git", "config", "user.name"]).stdout.strip()
    email = run(["git", "config", "user.email"]).stdout.strip()
    if not name or not email:
        raise CommitError(
            "could not resolve approver from git config "
            "(set user.name/user.email or pass --approved-by)"
        )
    return f"{name} <{email}>"


def build_message(description: str, authors: list[str], approver: str, reviewers: list[str]) -> str:
    """Assemble the commit message and its trailer block.

    Args:
        description: Commit subject.
        authors: Author model ids, in order. Deduplicated here.
        approver: Human approver as ``Name <email>``.
        reviewers: Agent reviewer model ids, appended after the human.

    Returns:
        str: The full commit message.

    Raises:
        CommitError: If no non-empty author model was supplied.
    """
    # dict.fromkeys dedupes while preserving first-seen order; a commit may
    # legitimately mix authors (the generator wrote impl, the adversary
    # wrote tests), so every distinct model gets an entry on one line.
    distinct_authors = list(dict.fromkeys(model for model in authors if model))
    if not distinct_authors:
        raise CommitError("no non-empty --author-model provided")

    coauthors = ", ".join(f"Copilot ({model}) <{COPILOT_EMAIL}>" for model in distinct_authors)
    reviewed = ", ".join(
        [approver] + [f"Copilot ({model}) <{COPILOT_EMAIL}>" for model in reviewers if model]
    )
    return (
        f"{description}\n\n"
        f"Co-authored-by: {coauthors}\n"
        f"Approved-by: {approver}\n"
        f"Reviewed-by: {reviewed}"
    )


def main(argv: list[str]) -> int:
    """Entry point.

    Raises:
        CommitError: On any refusal — not inside a work tree, the review gate
            not asserted, no description, no author model, or nothing staged.
    """
    args = parse_args(argv)

    authors = list(args.author_model)
    if not authors and os.environ.get("COPILOT_MODEL"):
        authors.append(os.environ["COPILOT_MODEL"])

    if not run(["git", "rev-parse", "--is-inside-work-tree"]).ok:
        raise CommitError("not inside a git work tree")

    # The human-review gate is asserted by the caller and is never inferred.
    if not args.confirm_reviewed:
        raise CommitError(
            "human-review gate: pass --confirm-reviewed only after a human has "
            "reviewed the staged changes"
        )
    if not args.description:
        raise CommitError("--description is required")
    if not authors:
        raise CommitError(
            "at least one --author-model is required (or set $COPILOT_MODEL); "
            "these are the model(s) that generated the staged code"
        )

    approver = resolve_approver(args.approved_by)

    # This skill never stages anything itself: the human decides exactly what
    # goes into the commit by staging it beforehand.
    if run(["git", "diff", "--cached", "--quiet"]).ok:
        raise CommitError(
            "nothing staged to commit — stage the changes you want committed first "
            "(e.g. git add <paths>); this skill never stages for you"
        )

    message = build_message(args.description, authors, approver, list(args.reviewer_model))

    committed = run(["git", "commit", "-m", message])
    if committed.stdout:
        print(committed.stdout)
    if not committed.ok:
        if committed.stderr:
            print(committed.stderr, file=sys.stderr)
        return committed.returncode

    short = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    print()
    print(f"Committed {short} on {branch}.")
    print("Not pushed — pushing is a separate, human-instructed step.")
    return 0


if __name__ == "__main__":
    run_main(main)
