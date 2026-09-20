#!/usr/bin/env python3
"""Remove a non-main Git worktree and optionally its local branch.

The command refuses the canonical worktree before requesting any destructive
Git operation.  It never touches shared agent-context targets: Git removes only
the symlinks that live in the removed worktree.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))

from skillkit.cli import info, run_main
from skillkit.errors import SkillError, UsageError
from skillkit.gitio import git, toplevel


def usage() -> None:
    """Print the public command usage.

    Returns:
        None.
    """
    print("""Usage: worktree_remove.py <branch> [--force] [--delete-branch]

  branch           Branch whose worktree should be removed.
  --force          Pass --force to 'git worktree remove' (allows dirty tree).
  --delete-branch  Also delete the local branch afterwards.
                   Combine with --force to force-delete an unmerged branch.

Refuses to remove the main worktree.""")


def git_or_raise(root: Path, *args: str) -> str:
    """Run Git, relaying its output and preserving a failing exit status.

    Args:
        root: Repository root passed to Git.
        *args: Git arguments after ``git -C <root>``.

    Returns:
        Captured standard output.

    Raises:
        SkillError: If Git exits unsuccessfully.
    """
    result = git(root, *args)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if not result.ok:
        raise SkillError(f"git {' '.join(args)} failed", code=result.returncode or 1)
    return result.stdout


def worktree_records(root: Path) -> tuple[Path | None, list[tuple[Path, str]]]:
    """Parse the canonical and branch-associated Git worktree paths.

    Args:
        root: Repository root.

    Returns:
        The first worktree path reported by Git, if present, and
        ``(path, branch)`` pairs. Detached worktrees are omitted from the
        branch pairs as Git's shell implementation did.

    Raises:
        SkillError: If Git cannot produce porcelain output.
    """
    result = git(root, "worktree", "list", "--porcelain")
    if not result.ok:
        raise SkillError("git worktree list failed", code=result.returncode or 1)
    main: Path | None = None
    records: list[tuple[Path, str]] = []
    current: Path | None = None
    prefix = "branch refs/heads/"
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree ") :])
            if main is None:
                main = current
        elif current is not None and line.startswith(prefix):
            records.append((current, line[len(prefix) :]))
    return main, records


def main(argv: list[str]) -> int:
    """Remove the worktree associated with one branch.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Process exit status.

    Raises:
        SkillError: If a requested Git operation fails.
        UsageError: If arguments are invalid.
    """
    if not argv or argv[0] in {"-h", "--help"}:
        usage()
        return 2 if not argv else 0

    branch = argv[0]
    force = False
    delete_branch = False
    for arg in argv[1:]:
        if arg == "--force":
            force = True
        elif arg == "--delete-branch":
            delete_branch = True
        else:
            print(f"ERROR: unknown argument: {arg}", file=sys.stderr)
            usage()
            return 2

    root = toplevel(Path.cwd())
    main_worktree, records = worktree_records(root)
    if main_worktree is None:
        raise SkillError("main worktree not found", code=3)
    target = next((path for path, name in records if name == branch), None)
    if target is None:
        raise SkillError(f"no worktree found for branch '{branch}'", code=3)
    if target == main_worktree:
        raise SkillError(f"refusing to remove the main worktree ({target})", code=4)

    info(f"Removing worktree {target}")
    remove_args = ["worktree", "remove"]
    if force:
        remove_args.append("--force")
    remove_args.append(str(target))
    git_or_raise(root, *remove_args)
    git_or_raise(root, "worktree", "prune")

    if delete_branch:
        if force:
            info(f"Force-deleting branch {branch}")
            git_or_raise(root, "branch", "-D", branch)
        else:
            info(f"Deleting branch {branch}")
            git_or_raise(root, "branch", "-d", branch)

    print("Done.")
    return 0


if __name__ == "__main__":
    run_main(main)
