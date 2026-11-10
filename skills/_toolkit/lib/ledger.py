#!/usr/bin/env python3
"""Per-file provenance and approval ledger for the developer's commit gate.

No change is committed unattributed, and nothing is committed that a human did
not approve at its current content. Both rules live here as checks rather than
as SQL an agent has to write correctly::

    ledger.py author   --repo <dir> --model <id> <path|AUTHORED=path>...
    ledger.py reviewer --repo <dir> --model <id> <path>...
    ledger.py approve  --repo <dir> --report <close-report.json>
    ledger.py commit-flags --repo <dir>
    ledger.py committed    --repo <dir>

``approve`` verifies a tuicr close report against the live tree, stages exactly
the reviewed paths into an empty index, and records the approval with the index
tree as its digest. ``commit-flags`` refuses unless the index still matches
that approval and every staged path has an author, then emits one
``AUTHOR_MODEL=`` / ``REVIEWER_MODEL=`` line per distinct model for
``git-commit``. ``committed`` stamps the approval and its provenance rows with the new commit,
so a later change to the same paths needs fresh attribution.

The database lives under ``$XDG_STATE_HOME/developer/ledger.sqlite``, keyed by
repository root, so it survives agent context compaction and session restarts.

Exit codes:
    0   Success.
    2   Usage error.
    3   Not a git repository, or the report is unreadable.
    4   A gate refused: the report, tree, index or ledger does not allow it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.cli import emit, run_main  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402
from skillkit.gitio import git, is_repo  # noqa: E402
from skillkit.paths import state_dir  # noqa: E402

#: Model IDs that name no model; recording one would be a false attribution.
PLACEHOLDERS = {"", "auto", "copilot-auto", "unknown"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS file_provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL, file_path TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('author','reviewer')),
    model_id TEXT NOT NULL, commit_sha TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL, head_sha TEXT NOT NULL, staged_digest TEXT NOT NULL,
    approver TEXT NOT NULL, report TEXT NOT NULL, files TEXT NOT NULL,
    commit_sha TEXT,
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
"""


def refuse(message: str, hint: str | None = None) -> SkillError:
    return SkillError(message, code=4, hint=hint)


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(state_dir("developer", create=True) / "ledger.sqlite")
    db.executescript(SCHEMA)
    return db


def out(repo: Path, *args: str) -> str:
    result = git(repo, *args)
    if result.returncode:
        raise refuse(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def staged(repo: Path) -> list[str]:
    return sorted(filter(None, out(repo, "diff", "--cached", "--name-only", "-z").split("\0")))


def model_id(model: str) -> str:
    if model.strip().lower() in PLACEHOLDERS:
        raise UsageError(f"not a concrete model ID: {model!r}")
    return model.strip()


def record(repo: Path, role: str, model: str, paths: list[str]) -> int:
    model = model_id(model)
    rows = [p.removeprefix("AUTHORED=") for p in paths]
    with connect() as db:
        db.executemany(
            "INSERT INTO file_provenance (repo, file_path, role, model_id) VALUES (?, ?, ?, ?)",
            [(str(repo), path, role, model) for path in rows])
    for path in rows:
        emit(role.upper(), f"{path} {model}")
    return 0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cmd_approve(repo: Path, report_path: Path) -> int:
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError) as exc:
        raise SkillError(f"unreadable close report {report_path}: {exc}", code=3) from exc
    if report.get("verdict") != "approved" or not report.get("approved"):
        raise refuse(f"report verdict is {report.get('verdict')!r}, not approved",
                     hint="; ".join(report.get("reasons") or []) or None)
    if report.get("unanswered"):
        raise refuse(f"{len(report['unanswered'])} comment(s) are unanswered")
    head = out(repo, "rev-parse", "HEAD")
    if not report.get("head_before") == report.get("head_after") == head:
        raise refuse("HEAD moved since the review", hint="reopen the review for the current HEAD")
    if staged(repo):
        raise refuse("the index is not empty", hint="unstage everything; approve stages the reviewed set")
    files = (report.get("marks") or {}).get("files") or []
    if not files or any(f.get("state") != "reviewed" for f in files):
        raise refuse("the report does not mark every file reviewed")
    for f in files:
        live = repo / f["path"]
        want = f.get("content_sha256")
        if (sha256(live) if live.is_file() else None) != want:
            raise refuse(f"{f['path']} changed since it was reviewed", hint="a changed diff needs a fresh review")
    paths = sorted(f["path"] for f in files)
    out(repo, "add", "--", *paths)
    if staged(repo) != paths:
        raise refuse(f"staged set {staged(repo)} differs from the reviewed set {paths}")
    approver = f"{out(repo, 'config', 'user.name')} <{out(repo, 'config', 'user.email')}>"
    with connect() as db:
        db.execute("INSERT INTO approvals (repo, head_sha, staged_digest, approver, report, files)"
                   " VALUES (?, ?, ?, ?, ?, ?)",
                   (str(repo), head, out(repo, "write-tree"), approver, str(report_path), json.dumps(paths)))
    emit("APPROVER", approver)
    for path in paths:
        emit("STAGED", path)
    return 0


def pending(db: sqlite3.Connection, repo: Path):
    return db.execute(
        "SELECT id, head_sha, staged_digest FROM approvals WHERE repo = ? AND commit_sha IS NULL"
        " ORDER BY id DESC LIMIT 1", (str(repo),)).fetchone()


def cmd_commit_flags(repo: Path) -> int:
    with connect() as db:
        approval = pending(db, repo)
        if not approval:
            raise refuse("no pending approval", hint="run: ledger.py approve --report <close-report>")
        _, head, digest = approval
        if out(repo, "rev-parse", "HEAD") != head or out(repo, "write-tree") != digest:
            raise refuse("the index or HEAD changed since approval", hint="a changed diff needs a fresh approval")
        paths = staged(repo)
        marks = ",".join("?" * len(paths))
        rows = db.execute(f"SELECT DISTINCT file_path, role, model_id FROM file_provenance"
                          f" WHERE repo = ? AND commit_sha IS NULL AND file_path IN ({marks})", (str(repo), *paths)).fetchall()
    authored = {path for path, role, _ in rows if role == "author"}
    if missing := [p for p in paths if p not in authored]:
        raise refuse(f"no author recorded for: {', '.join(missing)}",
                     hint="record the real author with ledger.py author; never default to yourself")
    for role, key in (("author", "AUTHOR_MODEL"), ("reviewer", "REVIEWER_MODEL")):
        for model in sorted({m for _, r, m in rows if r == role}):
            emit(key, model)
    return 0


def cmd_committed(repo: Path) -> int:
    with connect() as db:
        approval = pending(db, repo)
        if not approval:
            raise refuse("no pending approval")
        commit = out(repo, "rev-parse", "HEAD")
        if out(repo, "rev-parse", "HEAD^{tree}") != approval[2]:
            raise refuse("HEAD does not contain the approved tree")
        db.execute("UPDATE approvals SET commit_sha = ? WHERE id = ?", (commit, approval[0]))
        paths = json.loads(db.execute("SELECT files FROM approvals WHERE id = ?", (approval[0],)).fetchone()[0])
        db.execute(f"UPDATE file_provenance SET commit_sha = ? WHERE repo = ? AND commit_sha IS NULL"
                   f" AND file_path IN ({','.join('?' * len(paths))})", (commit, str(repo), *paths))
    emit("COMMIT", commit)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ledger.py", description="Provenance and approval ledger.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("author", "reviewer"):
        p = sub.add_parser(name, help=f"Record the {name} model of paths.")
        p.add_argument("--model", required=True)
        p.add_argument("paths", nargs="+", metavar="PATH")
    sub.add_parser("approve", help="Verify a close report and stage its files.").add_argument(
        "--report", required=True)
    sub.add_parser("commit-flags", help="Emit git-commit model flags for the approved index.")
    sub.add_parser("committed", help="Stamp the approval with HEAD.")
    for p in sub.choices.values():
        p.add_argument("--repo", default=".", help="Repository work tree.")
    args = parser.parse_args(argv)
    if not is_repo(args.repo):
        raise SkillError(f"not a git repository: {args.repo}", code=3)
    repo = Path(out(Path(args.repo), "rev-parse", "--show-toplevel"))
    if args.command in ("author", "reviewer"):
        return record(repo, args.command, args.model, args.paths)
    if args.command == "approve":
        return cmd_approve(repo, Path(args.report))
    if args.command == "commit-flags":
        return cmd_commit_flags(repo)
    return cmd_committed(repo)


if __name__ == "__main__":
    run_main(main)
