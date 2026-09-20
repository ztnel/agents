#!/usr/bin/env python3
"""Refresh branch-backed tuicr review refs before opening a review.

Fetches the selected remote, resolves branch names to fresh remote-tracking
refs, and fast-forwards a clean current HEAD when it is behind its upstream. A
dirty or diverged stale HEAD is rejected rather than reviewed misleadingly —
reviewing a stale snapshot silently shows the human the wrong diff.

The base defaults to the remote's own default branch (``origin/HEAD``) rather
than any hardcoded name, so the helper works in any repository.

Emits ``REVIEW_BASE_REF``, ``REVIEW_HEAD_REF`` and ``REVIEW_REVSET``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.cli import emit, run_main, warn  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402
from skillkit.gitio import git  # noqa: E402


def resolve_fresh_ref(repo: str, ref: str, remote: str) -> str:
    """Resolve *ref* to its freshest form after a fetch.

    A bare branch name that exists on *remote* resolves to the remote-tracking
    ref, so the review sees what the remote has rather than a stale local copy.

    Args:
        repo: Repository path.
        ref: Ref name, possibly already remote-qualified.
        remote: Remote name.

    Returns:
        str: The resolved ref name.

    Raises:
        SkillError: Exit 5 if the ref does not resolve to a commit.
    """
    if ref.startswith(f"{remote}/"):
        if not git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").ok:
            raise SkillError(f"base ref '{ref}' does not resolve after fetch", code=5)
        return ref
    if git(repo, "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{ref}").ok:
        return f"{remote}/{ref}"
    if not git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").ok:
        raise SkillError(f"base ref '{ref}' does not resolve after fetch", code=5)
    return ref


def resolve_base(repo: str, base_ref: str, remote: str) -> str:
    """Determine the review base.

    Precedence: explicit ``--base``, then ``$TUICR_BASE_REF``, then the remote's
    advertised default branch. Never a hardcoded branch name.

    Raises:
        SkillError: Exit 8 if the remote's default branch cannot be determined.
    """
    ref = base_ref or os.environ.get("TUICR_BASE_REF", "")
    if ref:
        return ref

    result = git(repo, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    if result.ok and result.stdout.strip():
        return result.stdout.strip()

    # The remote-tracking HEAD is only populated on clone; re-derive it for a
    # repo created another way (worktree, init + remote add).
    git(repo, "remote", "set-head", remote, "--auto")
    result = git(repo, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    if result.ok and result.stdout.strip():
        return result.stdout.strip()

    raise SkillError(f"could not determine {remote}'s default branch; pass --base <ref>", code=8)


def sync_head(repo: str, remote: str, allow_ff: bool) -> None:
    """Fast-forward a clean HEAD that is behind its upstream.

    Raises:
        SkillError: Exit 6 if HEAD has diverged, exit 7 if it is behind but the
            worktree is dirty. Both mean the review would misrepresent the
            branch, so they are refused rather than worked around.
    """
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()

    upstream = ""
    if branch and git(repo, "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}").ok:
        upstream = f"{remote}/{branch}"
    else:
        result = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        if result.ok:
            upstream = result.stdout.strip()
    if not upstream:
        return

    counts = git(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    if not counts.ok:
        return
    fields = counts.stdout.split()
    if len(fields) != 2:
        return
    local_ahead, upstream_ahead = int(fields[0]), int(fields[1])
    if upstream_ahead <= 0:
        return

    if local_ahead > 0:
        raise SkillError(f"HEAD has diverged from {upstream}; update/rebase before review", code=6)
    if not allow_ff:
        warn(f"HEAD is behind {upstream}; reviewing the local HEAD as-is (--no-ff)")
        return
    if git(repo, "status", "--porcelain").stdout.strip():
        raise SkillError(
            f"HEAD is behind {upstream}, but the worktree is dirty; update it before review",
            code=7,
        )
    git(repo, "merge", "--ff-only", upstream)


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(prog="refresh_review_refs.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--no-ff", dest="ff_head", action="store_false", default=True)
    known, extra = parser.parse_known_args(argv)
    if extra:
        raise SkillError(f"unknown argument '{extra[0]}'", code=2)

    repo = known.repo
    if not Path(repo).is_dir():
        raise SkillError(f"repo '{repo}' does not exist", code=3)
    if not git(repo, "rev-parse", "--git-dir").ok:
        raise SkillError(f"not a git repository: {repo}", code=3)
    if not git(repo, "remote", "get-url", known.remote).ok:
        raise SkillError(f"remote '{known.remote}' does not exist in {repo}", code=4)

    # Fetch output goes to stderr so stdout stays a clean metadata block.
    fetched = git(repo, "fetch", "--prune", known.remote)
    if fetched.stderr:
        print(fetched.stderr, file=sys.stderr)

    resolved_base = resolve_fresh_ref(repo, resolve_base(repo, known.base, known.remote), known.remote)

    if known.head == "HEAD":
        sync_head(repo, known.remote, known.ff_head)
        resolved_head = "HEAD"
    else:
        resolved_head = resolve_fresh_ref(repo, known.head, known.remote)

    emit("REVIEW_BASE_REF", resolved_base)
    emit("REVIEW_HEAD_REF", resolved_head)
    emit("REVIEW_REVSET", f"{resolved_base}...{resolved_head}")
    return 0


if __name__ == "__main__":
    run_main(main)
