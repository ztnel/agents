#!/usr/bin/env python3
"""Persistent workspace helpers for reviewed GitHub issue drafts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skillkit import paths  # noqa: E402

_DRAFT_FILE = "draft.md"
_METADATA_FILE = "metadata.json"
_BASELINE_DRAFT = "# Draft pending\n\nReplace this baseline with the reviewed issue draft.\n"
_BASELINE_METADATA = {"repo": "owner/repo"}


def workspace_root() -> Path:
    """Root directory for all persistent issue drafts."""
    return paths.state_dir("agents", "github-issue")


def draft_dir(draft_id: str) -> Path:
    """Directory holding one draft's git-backed workspace."""
    clean = draft_id.strip()
    if not clean:
        raise ValueError("draft_id must not be blank")
    if clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise ValueError("draft_id must be a simple path segment")
    return workspace_root() / clean


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _ensure_git_identity(workspace: Path) -> None:
    for key, value in {
        "user.name": "github-issue skill",
        "user.email": "github-issue@local.invalid",
    }.items():
        current = _git(workspace, "config", "--get", key, check=False)
        if current.returncode == 0 and current.stdout.strip():
            continue
        _git(workspace, "config", key, value)


def _has_head(workspace: Path) -> bool:
    return _git(workspace, "rev-parse", "--verify", "HEAD", check=False).returncode == 0


def _ensure_baseline_commit(workspace: Path) -> None:
    if _has_head(workspace):
        return
    paths.write_atomic(workspace / _DRAFT_FILE, _BASELINE_DRAFT)
    paths.write_json_atomic(workspace / _METADATA_FILE, _BASELINE_METADATA, indent=2)
    _git(workspace, "add", "--", _DRAFT_FILE, _METADATA_FILE)
    _git(workspace, "commit", "--quiet", "-m", "Initialize github-issue draft workspace")


def ensure_workspace(draft_id: str) -> Path:
    """Create and return the draft workspace, initializing git once."""
    root = workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    workspace = draft_dir(draft_id)
    workspace.mkdir(parents=True, exist_ok=True)
    if not (workspace / ".git").exists():
        subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
        _ensure_git_identity(workspace)
    else:
        _ensure_git_identity(workspace)
    _ensure_baseline_commit(workspace)
    return workspace


def save_draft(draft_id: str, markdown: str, metadata: dict[str, object]) -> Path:
    """Persist draft markdown and metadata in the draft workspace."""
    workspace = ensure_workspace(draft_id)
    paths.write_atomic(workspace / _DRAFT_FILE, markdown)
    paths.write_json_atomic(workspace / _METADATA_FILE, metadata, indent=2)
    return workspace / _DRAFT_FILE


def load_draft(draft_id: str) -> str:
    """Load persisted markdown for *draft_id*."""
    path = draft_dir(draft_id) / _DRAFT_FILE
    return path.read_text(encoding="utf-8")


def load_metadata(draft_id: str) -> dict[str, object]:
    """Load persisted metadata for *draft_id*."""
    path = draft_dir(draft_id) / _METADATA_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect or update the github-issue draft workspace.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("root", help="Print the workspace root.")

    ensure = subparsers.add_parser("ensure", help="Create or reuse a draft workspace.")
    ensure.add_argument("draft_id")

    save = subparsers.add_parser("save", help="Save markdown and metadata into a draft workspace.")
    save.add_argument("draft_id")
    save.add_argument("markdown_file")
    save.add_argument("metadata_file")

    load = subparsers.add_parser("load", help="Print saved draft markdown.")
    load.add_argument("draft_id")

    metadata = subparsers.add_parser("metadata", help="Print saved draft metadata as JSON.")
    metadata.add_argument("draft_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "root":
        print(workspace_root())
        return 0
    if args.command == "ensure":
        print(ensure_workspace(args.draft_id))
        return 0
    if args.command == "save":
        markdown = Path(args.markdown_file).read_text(encoding="utf-8")
        metadata = json.loads(Path(args.metadata_file).read_text(encoding="utf-8"))
        print(save_draft(args.draft_id, markdown, metadata))
        return 0
    if args.command == "load":
        print(load_draft(args.draft_id), end="")
        return 0
    if args.command == "metadata":
        print(json.dumps(load_metadata(args.draft_id), indent=2, sort_keys=True))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
