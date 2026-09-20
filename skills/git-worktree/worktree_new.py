#!/usr/bin/env python3
"""Create a sibling Git worktree and initialize its local agent context.

The default is a relative symlink back to the first worktree's gitignored
``.agents`` and ``AGENTS.md`` files.  A caller can explicitly request copied
context when branch-specific context is intentional.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))

from skillkit.cli import info, run_main
from skillkit.errors import SkillError, UsageError
from skillkit.gitio import branch_exists, git, remote_branch_exists, toplevel


def usage() -> None:
    """Print the public command usage.

    Returns:
        None.
    """
    print("""Usage: worktree_new.py [--copy-agent-context] <branch> [base]

  branch                 New branch name (or existing branch to check out).
  base                   Base ref for new branch. Default: origin/main, then
                         origin/master.
  --copy-agent-context   Copy the local agent context (.agents/, AGENTS.md)
                         into the new worktree instead of symlinking it. Use
                         when you deliberately want branch-specific context.

Creates a sibling worktree at ../<repo>-<sanitized-branch>, fetches origin,
and initialises submodules if .gitmodules is present. The gitignored local
agent context (.agents/ and every AGENTS.md) is symlinked back to the main
worktree by default so it has a single source of truth.""")


def git_or_raise(root: Path, *args: str) -> str:
    """Run Git, relay output, and retain its failure status.

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


def main_worktree(root: Path) -> Path:
    """Find Git's canonical first worktree.

    Args:
        root: Any path in the repository.

    Returns:
        Absolute path of the first worktree reported by Git.

    Raises:
        SkillError: If Git cannot report a worktree.
    """
    result = git(root, "worktree", "list", "--porcelain")
    if not result.ok:
        raise SkillError("git worktree list failed", code=result.returncode or 1)
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree ") :])
    raise SkillError("main worktree not found", code=3)


def agent_files(root: Path) -> list[Path]:
    """Return canonical relative ``AGENTS.md`` paths eligible for propagation.

    Args:
        root: Canonical main worktree.

    Returns:
        Relative file paths, excluding generated and Git metadata directories.
    """
    found: list[Path] = []
    for candidate in root.rglob("AGENTS.md"):
        relative = candidate.relative_to(root)
        if any(part in {"build", ".venv", ".git"} for part in relative.parts):
            continue
        found.append(relative)
    return found


def relative_target(destination: Path, canonical: Path) -> str:
    """Compute a portable relative symlink target.

    Args:
        destination: Link path that will contain the target.
        canonical: Existing canonical file or directory.

    Returns:
        Relative path from the link's parent to the canonical target.
    """
    return os.path.relpath(str(canonical), start=str(destination.parent))


def copy_agent_context(main: Path, target: Path) -> None:
    """Copy local agent context into a fresh worktree.

    Args:
        main: Canonical worktree that owns the context.
        target: Newly created worktree receiving independent copies.

    Returns:
        None.

    Side Effects:
        Creates files only in ``target``.
    """
    info("Copying local agent context (.agents, AGENTS.md)")
    canonical_agents = main / ".agents"
    if canonical_agents.is_dir():
        shutil.copytree(canonical_agents, target / ".agents", dirs_exist_ok=True, symlinks=True)
    for relative in agent_files(main):
        destination = target / relative
        if destination.parent.is_dir():
            shutil.copy2(main / relative, destination, follow_symlinks=False)


def link_agent_context(main: Path, target: Path) -> None:
    """Create relative links to the canonical local agent context.

    Args:
        main: Canonical worktree that owns the context.
        target: Newly created worktree receiving links.

    Returns:
        None.

    Side Effects:
        Creates relative symlinks only in ``target``.
    """
    info(f"Symlinking local agent context to main worktree ({main})")
    canonical_agents = main / ".agents"
    destination_agents = target / ".agents"
    if canonical_agents.is_dir():
        os.symlink(relative_target(destination_agents, canonical_agents), destination_agents)
    for relative in agent_files(main):
        destination = target / relative
        if destination.parent.is_dir():
            os.symlink(relative_target(destination, main / relative), destination)


def parse_arguments(argv: list[str]) -> tuple[bool, list[str]]:
    """Parse the worktree creation flags without changing positional semantics.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        A pair of ``(copy_agent_context, positional_arguments)``.

    Raises:
        UsageError: If an unrecognized option is supplied.
    """
    copy_context = False
    positional: list[str] = []
    remaining = list(argv)
    while remaining:
        arg = remaining.pop(0)
        if arg in {"-h", "--help"}:
            usage()
            raise SystemExit(0)
        if arg == "--copy-agent-context":
            copy_context = True
        elif arg == "--":
            positional.extend(remaining)
            break
        elif arg.startswith("--"):
            raise UsageError(f"unknown option: {arg}")
        else:
            positional.append(arg)
    if len(positional) > 2:
        raise UsageError(f"unexpected extra argument: {positional[2]}")
    return copy_context, positional


def main(argv: list[str]) -> int:
    """Create and initialize one sibling worktree.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Process exit status.

    Raises:
        SkillError: If Git or filesystem setup fails.
        UsageError: If no branch is supplied or an option is invalid.
    """
    try:
        copy_context, positional = parse_arguments(argv)
    except SystemExit as exc:
        return int(exc.code)
    if not positional:
        usage()
        return 2

    branch = positional[0]
    base = positional[1] if len(positional) > 1 else ""
    root = toplevel(Path.cwd())
    worktree_path = root.parent / f"{root.name}-{branch.replace('/', '-')}"
    if worktree_path.exists():
        raise SkillError(f"{worktree_path} already exists", code=3)

    info("Fetching origin")
    git_or_raise(root, "fetch", "origin", "--prune")
    if not base:
        if git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main").ok:
            base = "origin/main"
        elif git(root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/master").ok:
            base = "origin/master"
        else:
            raise SkillError(
                "neither origin/main nor origin/master found; pass base explicitly",
                code=4,
            )

    info(f"Creating worktree at {worktree_path}")
    if branch_exists(root, branch):
        git_or_raise(root, "worktree", "add", str(worktree_path), branch)
    elif remote_branch_exists(root, branch):
        git_or_raise(root, "worktree", "add", "--track", "-b", branch, str(worktree_path), f"origin/{branch}")
    else:
        git_or_raise(root, "worktree", "add", "-b", branch, str(worktree_path), base)

    if (worktree_path / ".gitmodules").is_file():
        info("Initialising submodules")
        git_or_raise(worktree_path, "submodule", "update", "--init", "--recursive")

    canonical = main_worktree(root)
    if canonical != worktree_path:
        if copy_context:
            copy_agent_context(canonical, worktree_path)
        else:
            link_agent_context(canonical, worktree_path)

    print()
    print(f"Worktree ready: {worktree_path}")
    print(f'Next: cd "{worktree_path}" and start a new agent session there.')
    return 0


if __name__ == "__main__":
    run_main(main)
