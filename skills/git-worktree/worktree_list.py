#!/usr/bin/env python3
"""List the Git worktrees belonging to the repository at the current path.

This is a deliberately thin, read-only wrapper around Git's stable worktree
porcelain display.  It retains the shell entry point so existing callers do not
need to know that the implementation is Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))

from skillkit.cli import run_main
from skillkit.errors import SkillError, UsageError
from skillkit.gitio import git, toplevel


def usage() -> None:
    """Print the public command usage.

    Returns:
        None.
    """
    print("Usage: worktree_list.py")
    print("Lists all git worktrees for the current repository.")


def main(argv: list[str]) -> int:
    """List worktrees after resolving the repository root.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Process exit status.

    Raises:
        SkillError: If Git cannot list the worktrees.
    """
    if argv:
        if len(argv) == 1 and argv[0] in {"-h", "--help"}:
            usage()
            return 0
        raise UsageError("worktree_list.py takes no arguments")

    root = toplevel(Path.cwd())
    result = git(root, "worktree", "list")
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if not result.ok:
        raise SkillError("git worktree list failed", code=result.returncode or 1)
    return 0


if __name__ == "__main__":
    run_main(main)
