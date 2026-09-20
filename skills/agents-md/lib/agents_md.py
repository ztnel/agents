#!/usr/bin/env python3
"""Read-only audit helpers for human-owned ``AGENTS.md`` context files.

The module locates, outlines, snapshots, and diffs context but deliberately
never edits, stages, commits, or pushes an ``AGENTS.md`` file itself.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.cli import run_main
from skillkit.paths import state_dir, working_file
from skillkit.proc import run

PROGRAM = "agents_md.py"
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
RULE_RE = re.compile(r"^[ \t]*(?:[-*]|[0-9]+[.)])[ \t]")


@dataclass(frozen=True)
class CommandError(Exception):
    """An expected CLI validation failure with its stable exit status.

    Attributes:
        message: User-facing diagnostic without a generic error prefix.
        code: Stable process exit status.
    """

    message: str
    code: int


def usage() -> None:
    """Print the public read-only helper contract.

    Returns:
        None.
    """
    print("""Usage:
  agents_md.py locate   [--repo <dir>] [--path <file>]
  agents_md.py outline  --file <file>
  agents_md.py snapshot --file <file> [--out <path>]
  agents_md.py diff     --file <file> --snapshot <path>

This helper never writes AGENTS.md. It only locates, outlines, snapshots, and
diffs human-owned context for review.""")


def fail(message: str, code: int = 1) -> CommandError:
    """Create a stable, caller-formatted validation error.

    Args:
        message: Human-facing failure reason.
        code: Process exit status.

    Returns:
        Exception used internally by subcommands.
    """
    return CommandError(message, code)


def take_value(argv: list[str], option: str, command: str) -> str:
    """Consume a required subcommand option value.

    Args:
        argv: Mutable remaining argument list.
        option: Flag requiring a value.
        command: Subcommand name for diagnostics.

    Returns:
        The following value.

    Raises:
        CommandError: If no value follows the option.
    """
    if not argv:
        raise fail(f"{command}: {option} needs a value", 2)
    return argv.pop(0)


def git_result(directory: Path, *args: str) -> tuple[bool, str]:
    """Run Git for facts only, avoiding command output leaks into the contract.

    Args:
        directory: Working directory for Git.
        *args: Git arguments.

    Returns:
        ``(success, stdout)``.
    """
    result = run(["git", "-C", str(directory), *args])
    return result.ok, result.stdout


def file_facts(path: Path) -> None:
    """Print symlink, canonical path, and Git tracking facts for one target.

    Args:
        path: Existing candidate path.

    Returns:
        None.
    """
    if not path.exists() and not path.is_symlink():
        print("  (missing)")
        return
    directory = path.parent
    canonical = path.resolve(strict=False)
    if path.is_symlink():
        print(f"  symlink -> {os.readlink(path)}")
        print(f"  canonical: {canonical}   (editing this changes the SHARED file for every worktree)")
    else:
        print(f"  canonical: {canonical}")
    inside, _ = git_result(directory, "rev-parse", "--is-inside-work-tree")
    if not inside:
        return
    ignored, _ = git_result(directory, "check-ignore", "-q", str(path))
    if ignored:
        print("  git: IGNORED (git diff will NOT show your edit — use this script's snapshot+diff)")
        return
    tracked, _ = git_result(directory, "ls-files", "--error-unmatch", str(path))
    print("  git: tracked" if tracked else "  git: untracked")


def command_locate(argv: Sequence[str]) -> int:
    """Locate explicit or repository-discovered AGENTS.md candidates.

    Args:
        argv: Arguments after ``locate``.

    Returns:
        Process exit status.

    Raises:
        CommandError: If options or supplied paths are invalid.
    """
    repo = ""
    specified = ""
    remaining = list(argv)
    while remaining:
        arg = remaining.pop(0)
        if arg == "--repo":
            repo = take_value(remaining, arg, "locate")
        elif arg == "--path":
            specified = take_value(remaining, arg, "locate")
        else:
            raise fail(f"locate: unknown arg '{arg}'", 2)
    if specified:
        target = Path(specified)
        if not target.exists() and not target.is_symlink():
            raise fail(f"path '{specified}' not found", 3)
        if target.name != "AGENTS.md":
            print(f"WARNING: '{specified}' is not named AGENTS.md — confirm this is intended.", file=sys.stderr)
        print(f"TARGET={specified}")
        file_facts(target)
        return 0

    directory = Path(repo or Path.cwd())
    if not directory.is_dir():
        raise fail(f"repo '{directory}' is not a directory", 3)
    inside, root_text = git_result(directory, "rev-parse", "--show-toplevel")
    root = Path(root_text) if inside and root_text else directory
    print(f"# AGENTS.md candidates under: {root}")
    candidates: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name != "node_modules")
        if "AGENTS.md" in files:
            candidate = Path(current) / "AGENTS.md"
            if not candidate.is_symlink():
                candidates.append(candidate)
    for candidate in sorted(candidates, key=str):
        print(f"CANDIDATE={candidate}")
        file_facts(candidate)
    if not candidates:
        print("  (none found — creating a new AGENTS.md requires an EXPLICIT human request)")
    current = directory
    while True:
        candidate = current / "AGENTS.md"
        if candidate.is_file():
            print(f"NEAREST={candidate}")
            break
        if current == current.parent:
            break
        current = current.parent
    return 0


def command_outline(argv: Sequence[str]) -> int:
    """Print section line numbers and rule-item counts for one context file.

    Args:
        argv: Arguments after ``outline``.

    Returns:
        Process exit status.

    Raises:
        CommandError: If the required file is missing.
    """
    path = ""
    remaining = list(argv)
    while remaining:
        arg = remaining.pop(0)
        if arg == "--file":
            path = take_value(remaining, arg, "outline")
        else:
            raise fail(f"outline: unknown arg '{arg}'", 2)
    if not path:
        raise fail("outline: --file is required", 2)
    target = Path(path)
    if not target.is_file():
        raise fail(f"outline: '{target}' not found", 3)
    text = target.read_text(encoding="utf-8", errors="replace")
    print(f"# Outline of {target}")
    print("# (audit EVERY section below before proposing any edit; find where a new rule belongs)")
    heading_line = 0
    heading = ""
    level = 0
    rules = 0

    def flush() -> None:
        """Print the accumulated section outline row.

        Returns:
            None.
        """
        if heading:
            print(f"{heading_line:5d}  {'  ' * (level - 1)}{heading}   [{rules} rule item(s)]")

    for number, line in enumerate(text.splitlines(), start=1):
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            heading = match.group(2)
            heading_line = number
            rules = 0
        elif RULE_RE.match(line):
            rules += 1
    flush()
    total = sum(1 for line in text.splitlines() if RULE_RE.match(line))
    print(f"# total top-level rule items: {total}")
    print(f"# total lines: {text.count(chr(10))}")
    return 0


def command_snapshot(argv: Sequence[str]) -> int:
    """Copy AGENTS.md content to a review snapshot without editing the source.

    Args:
        argv: Arguments after ``snapshot``.

    Returns:
        Process exit status.

    Raises:
        CommandError: If source or option values are invalid.
    """
    source = ""
    output = ""
    remaining = list(argv)
    while remaining:
        arg = remaining.pop(0)
        if arg == "--file":
            source = take_value(remaining, arg, "snapshot")
        elif arg == "--out":
            output = take_value(remaining, arg, "snapshot")
        else:
            raise fail(f"snapshot: unknown arg '{arg}'", 2)
    if not source:
        raise fail("snapshot: --file is required", 2)
    target = Path(source)
    if not target.is_file():
        raise fail(f"snapshot: '{target}' not found", 3)
    if output:
        snapshot = Path(output)
    else:
        root = state_dir("agents-md", "snapshots", create=True)
        snapshot = working_file("snapshot", ".AGENTS.md.orig", directory=root)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(target.read_bytes())
    print(f"SNAPSHOT={snapshot}")
    return 0


def command_diff(argv: Sequence[str]) -> int:
    """Print a unified before/after content diff, including gitignored files.

    Args:
        argv: Arguments after ``diff``.

    Returns:
        Always zero when inputs are valid, whether or not they differ.

    Raises:
        CommandError: If file or snapshot inputs are missing.
    """
    source = ""
    snapshot = ""
    remaining = list(argv)
    while remaining:
        arg = remaining.pop(0)
        if arg == "--file":
            source = take_value(remaining, arg, "diff")
        elif arg == "--snapshot":
            snapshot = take_value(remaining, arg, "diff")
        else:
            raise fail(f"diff: unknown arg '{arg}'", 2)
    if not source:
        raise fail("diff: --file is required", 2)
    if not snapshot:
        raise fail("diff: --snapshot is required", 2)
    target = Path(source)
    before = Path(snapshot)
    if not target.is_file():
        raise fail(f"diff: '{target}' not found", 3)
    if not before.is_file():
        raise fail(f"diff: snapshot '{before}' not found", 3)
    before_lines = before.read_text(encoding="utf-8", errors="surrogateescape").splitlines(keepends=True)
    after_lines = target.read_text(encoding="utf-8", errors="surrogateescape").splitlines(keepends=True)
    changes = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="a/AGENTS.md (before)",
            tofile="b/AGENTS.md (after)",
        )
    )
    if changes:
        sys.stdout.writelines(changes)
    else:
        print("# (no changes)")
    return 0


def main(argv: Sequence[str]) -> int:
    """Dispatch one read-only AGENTS.md audit subcommand.

    Args:
        argv: Command-line arguments excluding program name.

    Returns:
        Process exit status.
    """
    if not argv or argv[0] in {"-h", "--help"}:
        usage()
        return 0
    command, *rest = argv
    try:
        if command == "locate":
            return command_locate(rest)
        if command == "outline":
            return command_outline(rest)
        if command == "snapshot":
            return command_snapshot(rest)
        if command == "diff":
            return command_diff(rest)
        raise fail(f"unknown subcommand '{command}' (locate|outline|snapshot|diff)", 2)
    except CommandError as exc:
        print(f"{PROGRAM}: error: {exc.message}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    run_main(main)
