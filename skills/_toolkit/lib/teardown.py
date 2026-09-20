#!/usr/bin/env python3
"""Retire a merged feature's branch and worktree.

Removing the worktree also deletes the local feature branch and any submodule
clones inside it, so this is the whole teardown. ``--prune-remote`` additionally
deletes the branch on the remote.

``--merged`` is a caller-asserted gate: without it nothing is removed and the
script reports what the teardown *would* do. The flag exists because the
removal is a force-remove — it discards any work still sitting in the worktree —
so it must never be reachable by accident, only after the human has confirmed
the merge.

Emits ``REMOTE_DELETED``, ``LOCAL_BRANCH``, ``WORKTREE_REMOVED``.

Exit codes:
    2   Usage error.
    3   Worktree missing, or the git-worktree helper is not installed.
    4   Branch could not be determined, or the worktree is unsafe to remove.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.cli import emit, emit_all, info, run_main, warn  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402
from skillkit.gitio import git  # noqa: E402
from skillkit.proc import run  # noqa: E402

#: Sibling skill that owns worktree removal.
WORKTREE_REMOVE = Path.home() / ".agents" / "skills" / "git-worktree" / "worktree_remove.py"


def _main_worktree(worktree: Path) -> Path:
    """The primary worktree of *worktree*'s repository.

    ``git worktree remove`` must run from a checkout that survives the removal,
    so the helper is invoked from the primary worktree — the first entry
    ``git worktree list --porcelain`` reports.
    """
    listing = git(worktree, "worktree", "list", "--porcelain")
    if listing.ok:
        for line in listing.stdout.splitlines():
            if line.startswith("worktree "):
                return Path(line[len("worktree ") :].strip())
    return worktree


def _prune_remote(worktree: Path, branch: str, remote: str) -> None:
    """Delete *branch* on *remote* when it is present there."""
    if not git(worktree, "ls-remote", "--exit-code", "--heads", remote, branch).ok:
        emit("REMOTE_DELETED", f"(none: {branch} not on {remote})")
        return
    if git(worktree, "push", remote, "--delete", branch).ok:
        emit("REMOTE_DELETED", f"{remote}/{branch}")
    else:
        emit("REMOTE_DELETED", f"(failed: git push {remote} --delete {branch})")


def _remove_worktree(worktree: Path, branch: str) -> None:
    """Remove the worktree and its local branch via the git-worktree skill.

    ``--force --delete-branch`` keeps the operation on the caller-asserted
    teardown path; anything softer leaves a zombie worktree behind after the
    human has confirmed the merge.
    """
    if not (WORKTREE_REMOVE.is_file() and os.access(WORKTREE_REMOVE, os.X_OK)):
        raise SkillError(
            f"git-worktree remove helper not found at {WORKTREE_REMOVE}", code=3
        )
    result = run(
        [str(WORKTREE_REMOVE), branch, "--force", "--delete-branch"],
        cwd=str(_main_worktree(worktree)),
    )
    if result.stdout:
        print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    if not result.ok:
        raise SkillError(f"could not remove worktree for '{branch}'", code=4)


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="teardown.py",
        description="Retire a merged feature's branch and worktree.",
    )
    parser.add_argument("--worktree", default="", help="Feature worktree path.")
    parser.add_argument("--branch", default="", help="Feature branch (default: current).")
    parser.add_argument(
        "--merged",
        action="store_true",
        help="Confirm the feature has merged. Required for any removal.",
    )
    parser.add_argument(
        "--prune-remote", dest="prune_remote", action="store_true",
        help="Also delete the branch on the remote.",
    )
    parser.add_argument("--remote", default="origin", help="Remote for --prune-remote.")
    known, extra = parser.parse_known_args(argv)
    if extra:
        raise UsageError(f"unknown arg '{extra[0]}'")
    if not known.worktree:
        raise UsageError("--worktree is required")

    worktree = Path(known.worktree).expanduser()
    if not worktree.is_dir():
        raise SkillError(f"worktree '{known.worktree}' does not exist", code=3)

    branch = known.branch or git(worktree, "branch", "--show-current").stdout.strip()
    if not branch:
        raise SkillError(
            "could not determine the feature branch (detached HEAD?); pass --branch.",
            code=4,
        )
    emit("LOCAL_BRANCH", branch)

    if not known.merged:
        warn(
            "--merged not given: nothing removed. Teardown force-removes the "
            "branch and worktree, so it runs only after the merge is confirmed."
        )
        info(f"Would remove worktree '{worktree}' and branch '{branch}'.")
        if known.prune_remote:
            info(f"Would delete {known.remote}/{branch}.")
        emit_all({"REMOTE_DELETED": "(skipped: no --merged)",
                  "WORKTREE_REMOVED": "(skipped: no --merged)"})
        return 0

    if known.prune_remote:
        _prune_remote(worktree, branch, known.remote)
    else:
        emit("REMOTE_DELETED", "(skipped: no --prune-remote)")

    info(f"Removing worktree '{worktree}' (deletes local branch '{branch}')")
    _remove_worktree(worktree, branch)
    emit("WORKTREE_REMOVED", str(worktree))
    return 0


if __name__ == "__main__":
    run_main(main)
