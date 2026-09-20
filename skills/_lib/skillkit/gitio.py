"""git helpers.

``git`` is the single most-invoked external command across the skills (280
call sites). It is genuinely cross-platform and its porcelain is stable, so
this is a thin typed wrapper rather than a reimplementation — the value is in
not re-deriving ``rev-parse``/``for-each-ref`` invocations in every script, and
in :func:`default_remote_branch`, which is the de-hardcoding of ``develop``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .errors import SkillError
from .proc import CommandResult, run


def git(repo: str | Path, *args: str, check: bool = False) -> CommandResult:
    """Run ``git -C <repo> <args...>``."""
    return run(["git", "-C", str(repo), *args], check=check)


def is_repo(path: str | Path) -> bool:
    """Whether *path* is inside a git work tree."""
    return git(path, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def toplevel(path: str | Path) -> Path:
    """Root of the work tree containing *path*.

    Raises:
        SkillError: If *path* is not in a repository.
    """
    result = git(path, "rev-parse", "--show-toplevel")
    if not result.ok:
        raise SkillError(f"not a git repository: {path}")
    return Path(result.stdout.strip())


def head_sha(repo: str | Path, ref: str = "HEAD") -> str:
    """Full object name of *ref*.

    Raises:
        SkillError: If *ref* cannot be resolved.
    """
    result = git(repo, "rev-parse", ref)
    if not result.ok:
        raise SkillError(f"cannot resolve '{ref}' in {repo}")
    return result.stdout.strip()


def current_branch(repo: str | Path) -> str:
    """Checked-out branch name, or empty string when detached."""
    result = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    return result.stdout.strip() if result.ok else ""


def branch_exists(repo: str | Path, branch: str) -> bool:
    """Whether a local branch named *branch* exists."""
    return git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").ok


def remote_branch_exists(repo: str | Path, branch: str, remote: str = "origin") -> bool:
    """Whether *remote* has a branch named *branch*."""
    return git(repo, "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}").ok


def default_remote_branch(repo: str | Path, remote: str = "origin") -> str:
    """The remote's default branch, without hardcoding a name.

    Tries ``refs/remotes/<remote>/HEAD`` first, then asks the remote directly,
    then falls back to whichever conventional name actually exists. Hardcoding
    ``develop`` or ``main`` is what made these scripts project-specific.

    Raises:
        SkillError: If no default branch can be determined.
    """
    result = git(repo, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    if result.ok and result.stdout.strip():
        return result.stdout.strip().split("/", 1)[-1]

    result = git(repo, "ls-remote", "--symref", remote, "HEAD")
    if result.ok:
        for line in result.lines():
            if line.startswith("ref:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
                    return parts[1][len("refs/heads/") :]

    for candidate in ("main", "master", "develop", "trunk"):
        if remote_branch_exists(repo, candidate, remote):
            return candidate

    raise SkillError(f"cannot determine the default branch of remote '{remote}' in {repo}")


def fetch(repo: str | Path, remote: str = "origin", *extra: str) -> CommandResult:
    """Fetch from *remote*."""
    return git(repo, "fetch", remote, *extra)


def has_changes(repo: str | Path, *, staged: bool = True, unstaged: bool = True) -> bool:
    """Whether the work tree has changes of the requested kinds."""
    if unstaged and not git(repo, "diff", "--quiet").ok:
        return True
    if staged and not git(repo, "diff", "--cached", "--quiet").ok:
        return True
    return False


def changed_files(repo: str | Path, *revisions: str, staged: bool = False) -> list[str]:
    """Paths changed, relative to the repository root."""
    args = ["diff", "--name-only"]
    if staged:
        args.append("--cached")
    args.extend(revisions)
    result = git(repo, *args)
    return result.lines() if result.ok else []


def submodules(repo: str | Path) -> list[str]:
    """Paths of initialised submodules."""
    result = git(repo, "submodule", "--quiet", "foreach", "--recursive", "echo $sm_path")
    return result.lines() if result.ok else []


def merge_base_contains(repo: str | Path, ancestor: str, descendant: str) -> bool:
    """Whether *ancestor* is an ancestor of *descendant*."""
    return git(repo, "merge-base", "--is-ancestor", ancestor, descendant).ok


def blame_authors(repo: str | Path, path: str, lines: Sequence[tuple[int, int]] | None = None) -> list[str]:
    """Distinct author emails from ``git blame`` over *path*.

    Args:
        repo: Repository path.
        path: File to blame.
        lines: Optional ``(start, end)`` ranges to restrict the blame to.

    Returns:
        list[str]: Author emails in first-seen order.
    """
    args = ["blame", "--line-porcelain"]
    for start, end in lines or []:
        args += ["-L", f"{start},{end}"]
    args += ["--", path]
    result = git(repo, *args)
    if not result.ok:
        return []
    seen: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("author-mail "):
            email = line[len("author-mail ") :].strip().strip("<>")
            if email and email not in seen:
                seen.append(email)
    return seen
