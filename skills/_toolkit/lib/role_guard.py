#!/usr/bin/env python3
"""Enforce a sub-agent's declared file ownership, and report what it authored.

The CLI gives every sub-agent the full toolset — there is no per-agent path
allowlist — so "the adversary owns the tests, the generator owns the
implementation, neither touches the other's files" cannot be delegated to a
prompt. It is enforced here, after the fact, against ``git status``.

Two subcommands bracket one dispatch::

    role_guard.py snapshot --repo <dir> --out <file>
    role_guard.py enforce  --repo <dir> --baseline <file> --allow <glob>...

The snapshot is what makes this safe to run in a dirty tree. A working tree
routinely carries edits that have nothing to do with the dispatch, and reverting
those would destroy work nobody asked to be touched. Only paths whose content
changed *between* the snapshot and the enforce are considered the sub-agent's
doing; everything else is invisible to this script.

A violation the script can prove it introduced is reverted. A violation to a
path that was **already dirty** at snapshot time is reported but never
auto-reverted: the pre-existing content is not recoverable from git, so undoing
the sub-agent's edit would take the human's uncommitted work with it. That case
needs a human, so it exits non-zero and says so.

``enforce`` emits one ``AUTHORED=<path>`` line per accepted in-scope change.
That is the provenance ledger's author feed — the same ``git status`` walk that
proves the boundary already knows exactly which files the model wrote, so the
orchestrator inserts those rows without a second pass over the tree.

Exit codes:
    0   Clean — every change was in scope.
    2   Usage error.
    3   ``--repo`` is not a git repository, or the baseline is unreadable.
    4   Out-of-scope changes were found. Reverted ones are listed as
        ``REVERTED``; unrevertable ones as ``BLOCKED`` (these need a human).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.cli import emit, info, run_main, warn  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402
from skillkit.gitio import git, is_repo  # noqa: E402

#: Recorded instead of a digest when a path does not exist in the work tree.
ABSENT = "-"

#: Stand-in for a path the baseline never mentioned, i.e. one that matched HEAD
#: at snapshot time. It must differ from :data:`ABSENT`: a file that was clean
#: and a file that is gone are opposite situations, and conflating them makes a
#: sub-agent deleting someone else's file look like no change at all.
CLEAN = "clean"


def _digest(path: Path) -> str:
    """Content digest of *path*, or :data:`ABSENT` if it does not exist.

    Symlinks are hashed by their target rather than followed, so a sub-agent
    repointing a link is seen as a change.
    """
    if path.is_symlink():
        return hashlib.sha256(str(path.readlink()).encode()).hexdigest()
    if not path.is_file():
        return ABSENT
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dirty_state(repo: Path) -> dict[str, str]:
    """Map every path git reports as dirty to its current content digest.

    Uses ``-z`` because porcelain v1 quotes and escapes paths with unusual
    characters in its default output, and ``--no-renames`` so a rename is seen
    as the delete plus the add it is on disk — the two halves can fall on
    opposite sides of an ownership boundary.
    """
    result = git(repo, "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all")
    if not result.ok:
        raise SkillError(f"git status failed in {repo}: {result.stderr.strip()}")

    state: dict[str, str] = {}
    for entry in result.stdout.split("\0"):
        # Each record is 'XY <path>'; the status columns are fixed-width.
        if len(entry) < 4:
            continue
        rel = entry[3:]
        state[rel] = _digest(repo / rel)
    return state


def _to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a git-style path glob.

    ``**`` spans separators, ``*`` and ``?`` do not. A trailing ``/`` is
    treated as "this directory and everything under it", so ``tests/`` behaves
    the way a human means it.
    """
    if pattern.endswith("/"):
        pattern += "**"
    out = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            # 'a/**/b' should also match 'a/b', so let the glob absorb the
            # separator that follows it.
            if pattern.startswith("/", i):
                out.append("(?:/)?")
                i += 1
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def in_scope(rel: str, patterns: list[re.Pattern[str]]) -> bool:
    """Whether *rel* is covered by any allow pattern."""
    return any(pattern.match(rel) for pattern in patterns)


def revert(repo: Path, rel: str) -> bool:
    """Undo an out-of-scope change to *rel*, which was clean at snapshot time.

    Returns:
        bool: True if the path was restored.
    """
    tracked = git(repo, "ls-files", "--error-unmatch", "--", rel).ok
    if tracked:
        return git(repo, "checkout", "HEAD", "--", rel).ok
    target = repo / rel
    if target.is_file() or target.is_symlink():
        target.unlink()
        return True
    return not target.exists()


def cmd_snapshot(repo: Path, out: Path) -> int:
    """Record the pre-dispatch dirty state."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dirty_state(repo), indent=2, sort_keys=True))
    emit("BASELINE", out)
    return 0


def cmd_enforce(repo: Path, baseline_path: Path, allow: list[str], dry_run: bool) -> int:
    """Compare against the baseline, accept in-scope work, revert the rest."""
    try:
        baseline: dict[str, str] = json.loads(baseline_path.read_text())
    except (OSError, ValueError) as exc:
        raise SkillError(f"unreadable baseline {baseline_path}: {exc}") from exc

    patterns = [_to_regex(glob) for glob in allow]
    current = dirty_state(repo)

    # The baseline commonly lives in the session workspace, but nothing stops a
    # caller putting it inside the repo. It is this script's own bookkeeping,
    # never the sub-agent's work, so it must never be judged or reverted --
    # deleting it mid-flow would break the very dispatch it is guarding.
    try:
        self_rel = baseline_path.resolve().relative_to(repo).as_posix()
    except ValueError:
        self_rel = None

    authored: list[str] = []
    reverted: list[str] = []
    blocked: list[str] = []

    # A path dropping out of the dirty set is also a change (the sub-agent
    # reverted someone else's edit), so walk the union of both states.
    for rel in sorted(set(current) | set(baseline)):
        if rel == self_rel:
            continue
        before = baseline.get(rel, CLEAN)
        after = current.get(rel, CLEAN)
        if before == after:
            continue
        if in_scope(rel, patterns):
            authored.append(rel)
        elif rel in baseline:
            # Pre-existing uncommitted content; reverting would destroy it.
            blocked.append(rel)
        elif dry_run or revert(repo, rel):
            reverted.append(rel)
        else:
            blocked.append(rel)

    for rel in authored:
        emit("AUTHORED", rel)
    for rel in reverted:
        emit("REVERTED", rel)
        warn(f"out of scope, reverted: {rel}")
    for rel in blocked:
        emit("BLOCKED", rel)
        warn(f"out of scope and already dirty before dispatch, left alone: {rel}")

    if not reverted and not blocked:
        info(f"{len(authored)} change(s), all in scope")
        return 0
    return 4


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="role_guard.py",
        description="Enforce a sub-agent's declared file ownership.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Record the dirty state before a dispatch.")
    snap.add_argument("--repo", required=True, help="Repository work tree.")
    snap.add_argument("--out", required=True, help="Baseline file to write.")

    enforce = sub.add_parser("enforce", help="Check a dispatch's changes against its allowed paths.")
    enforce.add_argument("--repo", required=True, help="Repository work tree.")
    enforce.add_argument("--baseline", required=True, help="Baseline written by 'snapshot'.")
    enforce.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="GLOB",
        help="Path glob this role owns. Repeatable. '**' spans directories.",
    )
    enforce.add_argument(
        "--dry-run",
        action="store_true",
        help="Report violations without reverting anything.",
    )

    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if not is_repo(repo):
        raise SkillError(f"not a git repository: {repo}")

    if args.command == "snapshot":
        return cmd_snapshot(repo, Path(args.out))
    if not args.allow:
        raise UsageError("enforce requires at least one --allow glob")
    return cmd_enforce(repo, Path(args.baseline), args.allow, args.dry_run)


if __name__ == "__main__":
    run_main(main)
