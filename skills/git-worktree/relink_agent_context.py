#!/usr/bin/env python3
"""Convert copied agent context in existing worktrees into relative symlinks.

Dry-run is the default.  Divergent local context is moved aside before linking,
so migration never silently discards an independently changed ``.agents`` tree
or ``AGENTS.md`` file.
"""

from __future__ import annotations

import filecmp
import os
import shutil
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
    print("""Usage: relink_agent_context.py [--apply] [--all | <branch>]

  --apply   Perform the conversion. Without it, runs a dry-run that only
            reports what would change.
  --all     Process every worktree except the main one (default).
  <branch>  Process only the worktree checked out on this branch.

For each processed worktree, the main worktree's .agents/ and every AGENTS.md
(where the destination directory exists) are turned into relative symlinks back
to the main worktree. A regular file/dir whose content DIFFERS from the
canonical version is backed up to <path>.pre-symlink.bak before being replaced,
so local drift is never silently destroyed. Items already identical or already
correct symlinks are converted/skipped cleanly.""")


def parse_porcelain(root: Path) -> tuple[Path | None, list[tuple[Path, str]]]:
    """Parse the canonical and branch-bearing worktrees from Git output.

    Args:
        root: Repository root.

    Returns:
        The first worktree path reported by Git, if present, and
        ``(worktree_path, branch)`` pairs in Git's reported order.

    Raises:
        SkillError: If Git cannot list worktrees.
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


def agent_files(root: Path) -> list[Path]:
    """Find propagatable relative ``AGENTS.md`` paths under a canonical root.

    Args:
        root: Main worktree.

    Returns:
        Relative paths excluding build, virtual-environment, and Git metadata.
    """
    files: list[Path] = []
    for candidate in root.rglob("AGENTS.md"):
        relative = candidate.relative_to(root)
        if any(part in {"build", ".venv", ".git"} for part in relative.parts):
            continue
        files.append(relative)
    return files


def paths_match(canonical: Path, destination: Path) -> bool:
    """Compare two regular files or directory trees by content.

    Args:
        canonical: Canonical source path.
        destination: Existing worktree copy.

    Returns:
        True only when both paths have equivalent contents.
    """
    if canonical.is_dir() and destination.is_dir():
        comparison = filecmp.dircmp(canonical, destination)
        return directory_comparison_matches(comparison)
    if canonical.is_file() and destination.is_file():
        return filecmp.cmp(canonical, destination, shallow=False)
    return False


def directory_comparison_matches(comparison: filecmp.dircmp) -> bool:
    """Recursively determine whether a ``filecmp.dircmp`` has no differences.

    Args:
        comparison: Directory comparison to inspect.

    Returns:
        True if every descendant is identical.
    """
    if comparison.left_only or comparison.right_only or comparison.funny or comparison.diff_files:
        return False
    return all(directory_comparison_matches(child) for child in comparison.subdirs.values())


def remove_path(path: Path) -> None:
    """Remove one file, link, or directory without following a symlink.

    Args:
        path: Existing filesystem path.

    Returns:
        None.

    Side Effects:
        Deletes only ``path`` itself.
    """
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def relative_target(destination: Path, canonical: Path) -> str:
    """Return a relative link target from a destination's parent.

    Args:
        destination: Link path.
        canonical: Canonical target path.

    Returns:
        Relative target string.
    """
    return os.path.relpath(str(canonical), start=str(destination.parent))


def relink_one(destination: Path, canonical: Path, apply: bool) -> None:
    """Report or replace one copied context path with a relative symlink.

    Args:
        destination: Worktree-local context path.
        canonical: Main-worktree source path.
        apply: Whether mutation is authorized.

    Returns:
        None.

    Side Effects:
        With ``apply=True``, may back up and replace ``destination``.
    """
    wanted = relative_target(destination, canonical)
    if destination.is_symlink():
        actual = os.readlink(destination)
        if actual == wanted:
            print(f"    ok (already linked): {destination} -> {wanted}")
        else:
            print(f"    relink symlink: {destination} -> {wanted} (was {actual})")
            if apply:
                destination.unlink()
                os.symlink(wanted, destination)
        return

    if not os.path.lexists(destination):
        print(f"    create link: {destination} -> {wanted}")
        if apply:
            os.symlink(wanted, destination)
        return

    differs = not paths_match(canonical, destination)
    if differs:
        backup = Path(f"{destination}.pre-symlink.bak")
        print(f"    DRIFT: {destination} differs from canon; will back up to {backup}")
        if apply:
            if os.path.lexists(backup):
                remove_path(backup)
            shutil.move(str(destination), str(backup))
    else:
        print(f"    replace identical copy with link: {destination} -> {wanted}")
        if apply:
            remove_path(destination)
    if apply:
        os.symlink(wanted, destination)


def parse_arguments(argv: list[str]) -> tuple[bool, str | None]:
    """Parse migration selection flags.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        A pair of ``(apply, branch_or_none)``.

    Raises:
        UsageError: If an option or positional selector is invalid.
    """
    apply = False
    branch: str | None = None
    selector_seen = False
    for arg in argv:
        if arg in {"-h", "--help"}:
            usage()
            raise SystemExit(0)
        if arg == "--apply":
            apply = True
        elif arg == "--all":
            if selector_seen:
                raise UsageError("--all cannot be combined with a branch selector")
            branch = None
            selector_seen = True
        elif arg.startswith("--"):
            raise UsageError(f"unknown option: {arg}")
        elif not selector_seen:
            branch = arg
            selector_seen = True
        else:
            raise UsageError(f"unexpected extra argument: {arg}")
    return apply, branch


def main(argv: list[str]) -> int:
    """Dry-run or apply agent-context link migration.

    Args:
        argv: Command-line arguments excluding the program name.

    Returns:
        Process exit status.

    Raises:
        SkillError: If the repository/worktree state is invalid.
        UsageError: If options are invalid.
    """
    try:
        apply, branch = parse_arguments(argv)
    except SystemExit as exc:
        return int(exc.code)

    root = toplevel(Path.cwd())
    main, records = parse_porcelain(root)
    if main is None:
        raise SkillError("main worktree not found", code=3)
    if not main.is_dir():
        raise SkillError(f"main worktree not found: {main}", code=3)

    targets = [
        path
        for path, candidate_branch in records
        if path != main and (branch is None or candidate_branch == branch)
    ]
    if branch is not None and not targets:
        raise SkillError(f"no worktree found for branch '{branch}'", code=3)
    if not targets:
        print("No worktrees to process (only the main worktree exists).")
        return 0

    info(f"{'APPLYING' if apply else 'DRY RUN (no changes)'}. Source of truth: {main}")
    canonical_agents = main / ".agents"
    canonical_agent_files = agent_files(main)
    for target in targets:
        print(f"--> {target}")
        if not target.is_dir():
            print("    skip: worktree directory does not exist (stale/prunable entry); run 'git worktree prune'")
            continue
        if canonical_agents.is_dir():
            relink_one(target / ".agents", canonical_agents, apply)
        for relative in canonical_agent_files:
            destination = target / relative
            if destination.parent.is_dir():
                relink_one(destination, main / relative, apply)

    print()
    if apply:
        print("Done. Backups (if any) saved next to drifted files as *.pre-symlink.bak.")
    else:
        print("Dry run complete. Re-run with --apply to convert.")
    return 0


if __name__ == "__main__":
    run_main(main)
