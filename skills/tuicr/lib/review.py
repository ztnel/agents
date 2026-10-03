#!/usr/bin/env python3
"""Validated front door for the ``tuicr review`` operations agents perform.

Agents invoke ``tuicr`` free-hand from a shell, and the raw CLI punishes two
mistakes that are easy to make and hard to read:

1. ``--repo`` is a *subcommand* flag. ``tuicr review --repo <path>`` — the
   subcommand omitted — fails with ``unexpected argument '--repo' found`` and
   a usage block that never mentions the real problem.
2. A reply threads **positionally**. It appears under the comment it answers
   only when its file, start line, end line and side all match exactly;
   dropping ``--end-line`` silently re-anchors it to a single line and the
   reply lands somewhere else in the review.

This wrapper removes both classes of error rather than documenting them: the
subcommand is required by the parser (so the failure is a readable message
naming the valid choices), and ``reply`` takes the *id* of the comment being
answered and copies that comment's anchor verbatim.
"""

from __future__ import annotations

import argparse
import json
import os
import stat as statmod
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import gitio, tuicrio  # noqa: E402
from skillkit.cli import run_main  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402
import questions  # noqa: E402
import hashlib

#: tuicr re-renders every comment in a session on each frame, so total comment
#: text — not any single comment — drives the human's TUI latency. Long replies
#: are the controllable half of that budget.
MAX_REPLY_CHARS = 1500
EXIT_REVIEW_INCOMPLETE = 5
BLOCKING_STATES = frozenset({"unreviewed", "absent", "stale"})


def find_comment(repo: str, session: str, comment_id: str) -> tuicrio.Comment:
    """The comment in *session* whose id is *comment_id*.

    Raises:
        UsageError: No comment carries that id, listing what is available so
            the caller can correct the id without a second round trip.
    """
    found = tuicrio.comments(repo, session)
    for comment in found:
        if comment.id == comment_id:
            return comment
    known = ", ".join(c.id for c in found) or "none"
    raise UsageError(f"no comment with id {comment_id!r} in session {session!r} (available: {known})")


def resolve_session(repo: str, session: str | None) -> str:
    """*session* when given, else the repo's single active session."""
    if session:
        return session
    return tuicrio.active_session(repo).slug


def cmd_list(args: argparse.Namespace) -> int:
    """Print the review sessions known for the repo."""
    sessions = tuicrio.list_sessions(args.repo)
    if args.json:
        print(json.dumps([s.raw for s in sessions], indent=2))
        return 0
    if not sessions:
        print("no review sessions")
        return 0
    for session in sessions:
        marker = "active" if session.active else "inactive"
        print(f"{session.slug}\t{marker}\tcomments={session.comment_count}")
    return 0


def cmd_comments(args: argparse.Namespace) -> int:
    """Print the comments in a session, ids included so replies can target them."""
    session = resolve_session(args.repo, args.session)
    found = tuicrio.comments(args.repo, session)
    if args.unanswered:
        replies = tuicrio.replies_by_anchor(found)
        registered = questions.registered_ids(args.repo, session)
        found = [
            c for c in found if c.id not in registered
            and c.comment_type != "reply" and not tuicrio.is_answered(c, replies)
        ]
    if args.json:
        print(json.dumps([c.raw for c in found], indent=2))
        return 0
    if not found:
        print("no comments" if not args.unanswered else "no unanswered comments")
        return 0
    for comment in found:
        print(f"{comment.id}\t{comment.anchor}\t[{comment.comment_type or '?'}]")
        for line in comment.content.splitlines() or [""]:
            print(f"    {line}")
    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    """Reply to a comment, inheriting its anchor exactly."""
    body = args.body
    if len(body) > MAX_REPLY_CHARS:
        raise UsageError(
            f"reply is {len(body)} chars, over the {MAX_REPLY_CHARS}-char budget; "
            "post the verdict plus the decisive point here and give the human the rest directly"
        )
    session = resolve_session(args.repo, args.session)
    parent = find_comment(args.repo, session, args.to)
    tuicrio.reply_to(args.repo, session, parent, body, username=args.username)
    print(f"REPLIED_TO={parent.id}")
    print(f"ANCHOR={parent.anchor}")
    return 0


def cmd_question(args: argparse.Namespace) -> int:
    session = resolve_session(args.repo, args.session)
    if args.command == "register-question":
        questions.register(args.repo, session, args.comment_id)
    else:
        questions.resolve(args.repo, session, args.comment_id, args.answer_id)
    return 0


def _resolve_session_obj(repo: str, slug: str | None) -> tuicrio.Session:
    """Resolve the full session record needed to read persisted marks."""
    return tuicrio.session_by_slug(repo, slug) if slug else tuicrio.active_session(repo)


def _status_map(repo: str, *, staged: bool) -> dict[str, str]:
    """Return the current change set as path-to-status using NUL records."""
    if staged:
        result = gitio.git(
            repo, "diff", "--cached", "--name-status", "-z", "--ignore-submodules=none"
        )
        if not result.ok:
            raise UsageError(f"git diff --cached failed: {result.stderr.strip()}")
        fields = [field for field in result.stdout.split("\0") if field]
        statuses: dict[str, str] = {}
        names = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied"}
        index = 0
        while index < len(fields):
            code = fields[index][:1]
            if code in {"R", "C"}:
                if index + 2 >= len(fields):
                    raise UsageError("git returned an incomplete rename/copy record")
                statuses[fields[index + 2]] = names[code]
                index += 3
            else:
                if index + 1 >= len(fields):
                    raise UsageError("git returned an incomplete status record")
                statuses[fields[index + 1]] = names.get(code, code.lower())
                index += 2
        return statuses

    result = gitio.git(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if not result.ok:
        raise UsageError(f"git status failed: {result.stderr.strip()}")
    fields = [field for field in result.stdout.split("\0") if field]
    statuses = {}
    names = {
        "A": "added", "M": "modified", "D": "deleted", "R": "renamed",
        "C": "copied", "T": "type-changed", "U": "unmerged",
    }
    index = 0
    while index < len(fields):
        entry = fields[index]
        if len(entry) < 4:
            raise UsageError(f"git returned an invalid status record: {entry!r}")
        code, path = entry[:2], entry[3:]
        if code == "??":
            statuses[path] = "added"
            index += 1
            continue
        letter = code[0] if code[0] != " " else code[1]
        if letter in {"R", "C"}:
            if index + 1 >= len(fields):
                raise UsageError("git returned an incomplete rename/copy record")
            statuses[fields[index + 1]] = names[letter]
            index += 2
            continue
        statuses[path] = names.get(letter, letter.lower())
        index += 1
    return statuses


def _same_status(recorded: str, current: str) -> bool:
    """Whether tuicr and Git describe compatible change kinds."""
    value = recorded.strip().lower()
    if not value or value in {"unknown", "none"}:
        return True
    aliases = {
        "added": {"added", "add", "new", "a", "untracked"},
        "modified": {"modified", "modify", "m"},
        "deleted": {"deleted", "delete", "removed", "d"},
        "renamed": {"renamed", "rename", "r", "modified"},
        "copied": {"copied", "copy", "c", "modified", "added"},
    }
    return value in aliases.get(current, {current})


def _submodule_changed_after(path: Path, saved_at: float) -> bool | None:
    """Check the commit pointer age for a directory-backed Git checkout."""
    marker = path / ".git"
    try:
        if marker.is_file():
            text = marker.read_text(encoding="utf-8", errors="replace").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = (path / text.split(":", 1)[1].strip()).resolve()
        elif marker.is_dir():
            gitdir = marker
        else:
            return None
        head = gitdir / "HEAD"
        head_stat = head.stat()
        newest = max(head_stat.st_mtime, head_stat.st_ctime)
        text = head.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("ref:"):
            ref = gitdir / text.split(":", 1)[1].strip()
            target = ref if ref.exists() else gitdir / "packed-refs"
            target_stat = target.stat()
            newest = max(newest, target_stat.st_mtime, target_stat.st_ctime)
        return newest > saved_at
    except OSError:
        return None


def _classify(
    repo: str,
    path: str,
    status: str,
    reviews: dict[str, tuicrio.FileReview],
    saved_at: float,
) -> tuple[str, str]:
    """Classify one current changed path against the persisted human mark."""
    review = reviews.get(path)
    if review is None:
        return "absent", "the review surface never registered this changed file"
    if not review.reviewed:
        if review.reviewed_hunks:
            return "unreviewed", f"{review.reviewed_hunks} hunk(s) marked, but not the file"
        return "unreviewed", "not marked reviewed"
    if not _same_status(review.status, status):
        return "stale", f"Git reports {status}, but tuicr showed {review.status}"

    target = Path(repo) / path
    try:
        file_stat = target.lstat()
    except OSError:
        if status == "deleted":
            return "reviewed", ""
        return "stale", "current content cannot be read"
    if statmod.S_ISDIR(file_stat.st_mode):
        moved = _submodule_changed_after(target, saved_at)
        if moved is None:
            return "stale", "submodule pointer freshness cannot be established"
        if moved:
            return "stale", "submodule pointer changed after the session was saved"
        return "reviewed", ""
    if max(file_stat.st_mtime, file_stat.st_ctime) > saved_at:
        return "stale", "file changed after the session was saved"
    return "reviewed", ""


def cmd_reviewed(args: argparse.Namespace) -> int:
    """Report human file marks and optionally gate the current change set."""
    session = _resolve_session_obj(args.repo, args.session)
    reviews = tuicrio.file_reviews(session)
    saved_at = tuicrio.session_saved_at(session)
    statuses = _status_map(args.repo, staged=args.staged)
    rows = [
        (path, *_classify(args.repo, path, status, reviews, saved_at))
        for path, status in sorted(statuses.items())
    ]
    digests = {}
    for index, (path, state, why) in enumerate(rows):
        target = Path(args.repo) / path
        if state == "reviewed" and target.is_file():
            try:
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError as exc:
                rows[index] = (path, "stale", f"current content cannot be read: {exc}")
                continue
            state, why = _classify(args.repo, path, statuses[path], reviews, saved_at)
            rows[index] = (path, state, why)
            if state == "reviewed":
                digests[path] = digest
    blocked = [row for row in rows if row[1] in BLOCKING_STATES]
    if args.unreviewed:
        rows = blocked
    ok = bool(statuses) and not blocked

    if args.json:
        print(
            json.dumps(
                {
                    "session": session.slug,
                    "session_path": session.path,
                    "session_active": session.active,
                    "staged": args.staged,
                    "ok": ok,
                    "files": [
                        {"path": path, "state": state, "why": why,
                         **({"content_sha256": digests[path]} if path in digests else {})}
                        for path, state, why in rows
                    ],
                },
                indent=2,
            )
        )
    else:
        print(f"SESSION={session.slug}")
        for path, state, why in rows:
            print(f"{state}\t{path}" + (f"\t{why}" if why else ""))
        if not rows:
            print("(no changed files)")

    if args.gate and not ok:
        reason = "change set is empty" if not statuses else ", ".join(row[0] for row in blocked)
        raise SkillError(
            f"review incomplete for session '{session.slug}': {reason}",
            code=EXIT_REVIEW_INCOMPLETE,
        )
    return 0


def _add_shared(parser: argparse.ArgumentParser, *, suppress: bool) -> None:
    """Attach the ``--repo`` / ``--session`` flags to *parser*.

    They are attached to the top-level parser *and* to every subparser so they
    are accepted on either side of the subcommand. Flag position is the whole
    reason the raw CLI is error-prone here; making it irrelevant removes the
    mistake rather than relabelling it.

    Args:
        suppress: Set on the subparser copies. ``SUPPRESS`` leaves the
            attribute unset when the flag is absent, so the top-level value
            stands instead of being overwritten with a default.
    """
    parser.add_argument(
        "--repo",
        default=argparse.SUPPRESS if suppress else ".",
        help="repository path (default: current directory)",
    )
    parser.add_argument(
        "--session",
        default=argparse.SUPPRESS if suppress else None,
        help="session slug (default: the active session)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review.py",
        description="Validated tuicr review operations (list / comments / reply / reviewed).",
    )
    _add_shared(parser, suppress=False)
    # required=True turns the omitted-subcommand mistake into a message that
    # names the valid choices, instead of tuicr's misleading unexpected-argument
    # error.
    sub = parser.add_subparsers(
        dest="command", required=True, metavar="{list,comments,reply,reviewed}"
    )

    p_list = sub.add_parser("list", help="list review sessions for the repo")
    _add_shared(p_list, suppress=True)
    p_list.add_argument("--json", action="store_true", help="emit raw JSON")
    p_list.set_defaults(func=cmd_list)

    p_comments = sub.add_parser("comments", help="show comments in a session")
    _add_shared(p_comments, suppress=True)
    p_comments.add_argument(
        "--unanswered",
        action="store_true",
        help="only comments that have no reply after them",
    )
    p_comments.add_argument("--json", action="store_true", help="emit raw JSON")
    p_comments.set_defaults(func=cmd_comments)

    p_reply = sub.add_parser("reply", help="reply to a comment at its exact anchor")
    _add_shared(p_reply, suppress=True)
    p_reply.add_argument("--to", required=True, metavar="COMMENT_ID", help="id of the comment being answered")
    p_reply.add_argument("--username", help="posting identity (your model id)")
    p_reply.add_argument("body", help="reply text")
    p_reply.set_defaults(func=cmd_reply)

    p_reviewed = sub.add_parser("reviewed", help="read and verify human file-review marks")
    _add_shared(p_reviewed, suppress=True)
    p_reviewed.add_argument("--staged", action="store_true", help="inspect only staged changes")
    p_reviewed.add_argument("--gate", action="store_true", help="exit 5 unless every changed file passes")
    p_reviewed.add_argument("--unreviewed", action="store_true", help="show only blocking files")
    p_reviewed.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_reviewed.set_defaults(func=cmd_reviewed)

    for name in ("register-question", "resolve-question"):
        command = sub.add_parser(name, help="track a question directed to the human")
        _add_shared(command, suppress=True)
        command.add_argument("--comment-id", required=True)
        if name == "resolve-question":
            command.add_argument("--answer-id", required=True)
        command.set_defaults(func=cmd_question)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except tuicrio.TuicrError as exc:
        raise SkillError(str(exc)) from None


if __name__ == "__main__":
    run_main(main)
