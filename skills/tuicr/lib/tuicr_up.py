#!/usr/bin/env python3
"""Run a tuicr review in the current terminal and report how it closed.

Refreshes the review refs (so the diff reflects the remote, not a stale local
copy), runs tuicr in the foreground over the resolved revset, and, after tuicr
exits, persists the deterministic close report. The ``tuicr-feedback`` MCP
server delivers that report to the agent as the review's closure event.

This script never drives a terminal multiplexer. Run it wherever the human will
review: the caller's agent opens a pane for it (for example through the tmux
skill), or the human runs it in a terminal of their own.

Usage::

    tuicr_up.py [directory]

Arguments:
    directory: Git repository to review. Default: current directory.

Environment variables:
    TUICR_BASE_REF: Base ref for the review revset. Default: the remote's
        default branch — never a hardcoded name, so this works in any repo.
    TUICR_HEAD_REF: Head ref for the review revset. Default: ``HEAD``.
    TUICR_REMOTE: Remote refreshed before a branch review. Default: ``origin``.
"""

from __future__ import annotations

import argparse
import os
import json
import subprocess
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import paths  # noqa: E402
from skillkit.approval import ApprovalReceipt, ReviewedFile  # noqa: E402
from skillkit.cli import parse_metadata, run_main  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402
from skillkit.gitio import git  # noqa: E402
from skillkit.lock import PidFile  # noqa: E402
from skillkit.proc import run, which  # noqa: E402
from skillkit import workqueue  # noqa: E402
import questions  # noqa: E402

#: ANSI colours for the `[tuicr]` log prefix. Suppressed when stdout is not a
#: TTY so captured output stays clean.
_GREEN = "\033[0;32m"
_YELLOW = "\033[1;33m"
_RED = "\033[0;31m"
_RESET = "\033[0m"
_SLUG_RETRY_DELAY = 0.1
_SLUG_RETRY_LIMIT = 20
#: First tuicr that lists a review before it is saved; older ones hide it from
#: ``tuicr review list`` until the first comment, so agents cannot connect.
MIN_TUICR = (0, 28)
#: Orientation comments that survive closure; every settled comment is wiped.
KEPT_TYPES = frozenset({"description", "review-note"})


def _log(colour: str, message: str, stream=sys.stdout) -> None:
    """Print a ``[tuicr]`` prefixed line, coloured only for a terminal."""
    if stream.isatty():
        print(f"{colour}[tuicr]{_RESET} {message}", file=stream, flush=True)
    else:
        print(f"[tuicr] {message}", file=stream, flush=True)


def log_info(message: str) -> None:
    """Informational progress line."""
    _log(_GREEN, message)


def log_warn(message: str) -> None:
    """Warning line."""
    _log(_YELLOW, message)


def log_error(message: str) -> None:
    """Error line."""
    _log(_RED, message, stream=sys.stderr)


def review_lock(directory: str) -> PidFile:
    """Pidfile held by the launcher while it reviews *directory*.

    Scoped to this checkout deliberately: a global check would block reviewing
    a second repository at the same time.
    """
    return PidFile(paths.state_dir("tuicr", "running", create=True) / f"{paths.short_hash(directory)}.pid")


def resolve_revset(target_dir: str) -> str:
    """Refresh the review refs and return the resolved revset.

    Raises:
        SkillError: If the refresh helper is missing or fails.
    """
    helper = Path(__file__).resolve().parent / "refresh_review_refs.py"
    if not helper.is_file():
        raise SkillError(f"Review-ref refresh helper not found: {helper}")

    args = [
        sys.executable, str(helper),
        "--repo", target_dir,
        "--head", os.environ.get("TUICR_HEAD_REF", "HEAD"),
        "--remote", os.environ.get("TUICR_REMOTE", "origin"),
    ]
    base = os.environ.get("TUICR_BASE_REF", "")
    if base:
        args += ["--base", base]

    result = run(args)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if not result.ok:
        raise SkillError("could not refresh review refs", code=result.returncode)

    revset = parse_metadata(result.stdout).get("REVIEW_REVSET", "")
    if not revset:
        raise SkillError("refresh helper did not emit REVIEW_REVSET")
    return revset


def warn_outdated() -> None:
    """Warn the human when the installed tuicr is older than :data:`MIN_TUICR`."""
    words = run(["tuicr", "--version"]).stdout.split()
    try:
        installed = tuple(int(part) for part in words[-1].split(".")[:2])
    except (IndexError, ValueError):
        return
    if installed < MIN_TUICR:
        warnings.warn(
            f"tuicr {words[-1]} hides unsaved reviews from agents; upgrade to "
            f"{'.'.join(map(str, MIN_TUICR))} or newer (brew upgrade tuicr)",
            stacklevel=2,
        )


def active_review_slug(target_dir: str) -> str:
    """Resolve the single active tuicr review session for *target_dir*.

    A new review window is expected to make exactly one session active. If the
    session has not settled yet, the helper retries briefly before failing so
    the launcher does not race the TUI startup.
    """
    for attempt in range(_SLUG_RETRY_LIMIT):
        result = run(["tuicr", "review", "list", "--repo", target_dir])
        if result.ok:
            try:
                sessions = json.loads(result.stdout or "[]")
            except json.JSONDecodeError:
                sessions = []
            active = [item.get("slug", "") for item in sessions if item.get("active")]
            active = [slug for slug in active if slug]
            if len(active) == 1:
                return active[0]
            if len(active) > 1:
                raise SkillError(
                    f"multiple active tuicr sessions for {target_dir}: {', '.join(active)}"
                )
        if attempt + 1 < _SLUG_RETRY_LIMIT:
            time.sleep(_SLUG_RETRY_DELAY)
    raise SkillError(f"could not resolve an active tuicr session for {target_dir}")


def _read_json_output(result) -> dict:
    """Decode a helper's JSON stdout, returning an error-shaped object."""
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": result.stderr.strip() or "invalid helper output"}
    return value if isinstance(value, dict) else {"ok": False, "error": "helper returned non-object JSON"}


def review_verdict(target_dir: str, slug: str, head_before: str, exit_code: int | None) -> dict:
    """Compute and persist the deterministic verdict for a closed review."""
    helper = Path(__file__).resolve().parent / "review.py"
    marks_result = run([
        sys.executable, str(helper),
        "--repo", target_dir,
        "--session", slug,
        "reviewed", "--gate", "--json",
    ])
    marks = _read_json_output(marks_result)
    unanswered = questions.unanswered(target_dir, slug)
    head_result = git(target_dir, "rev-parse", "HEAD")
    head_after = head_result.stdout.strip() if head_result.ok else ""

    reasons = []
    if exit_code != 0:
        reasons.append("tuicr did not exit cleanly")
    if not head_after or head_after != head_before:
        reasons.append("HEAD changed during review")
    if not marks.get("ok"):
        reasons.append("not every changed file is reviewed at current content")
    if unanswered:
        reasons.append("questions to the human remain unanswered")
    queued = workqueue.pending(
        workqueue.default_inbox("tuicr-feedback"), workqueue.scope(target_dir, slug))
    if queued:
        reasons.append("accepted work is queued; apply it and open a new review round")
    approved = not reasons
    if approved:
        verdict = "approved"
    elif exit_code != 0:
        verdict = "aborted"
    else:
        verdict = "changes_queued" if queued else "incomplete"
    return {
        "approved": approved,
        "verdict": verdict,
        "approval": ApprovalReceipt(
            workspace=str(Path(target_dir).resolve()),
            head=head_after,
            approved=approved,
            files=tuple(
                ReviewedFile(file["path"], file["content_sha256"])
                for file in marks.get("files", [])
                if file.get("state") == "reviewed" and file.get("content_sha256")
            ),
        ).to_dict(),
        "repo": target_dir,
        "session": slug,
        "head_before": head_before,
        "head_after": head_after,
        "tuicr_exit_code": exit_code,
        "marks": marks,
        "unanswered": unanswered,
        "queued": queued,
        "reasons": reasons,
    }


def anchor(item: dict) -> dict:
    start = item.get("start_line") or 0
    return {
        "path": item.get("path") or "", "start_line": start,
        "end_line": item.get("end_line") or start, "side": item.get("side") or "new",
    }


def anchor_key(item: dict) -> str:
    return json.dumps(anchor(item), sort_keys=True)


def settled(comments: list[dict], registered: set[str], answered: set[str]) -> list[str]:
    """Ids of comments the review has settled: replies and what they answer.

    A later reply at the same anchor answers a comment. Answered questions
    and their answers are settled; unanswered questions, unanswered comments
    and orientation comments are not.
    """
    replies: dict[str, list[str]] = {}
    for item in comments:
        if item.get("comment_type") == "reply":
            replies.setdefault(anchor_key(item), []).append(item["created_at"])
    return [
        item["id"] for item in comments
        if item.get("comment_type") not in KEPT_TYPES
        and (item["id"] not in registered or item["id"] in answered)
        and (
            item.get("comment_type") == "reply" or item["id"] in answered
            or any(stamp > item["created_at"] for stamp in replies.get(anchor_key(item), []))
        )
    ]


def wipe_settled(target_dir: str, slug: str) -> list[str]:
    """Delete settled comments: their anchors may be stale once work lands."""
    listed = run(["tuicr", "review", "comments", "--repo", target_dir, "--session", slug])
    if not listed.ok:
        raise SkillError(f"could not read comments to wipe: {listed.stderr.strip()}")
    comments = json.loads(listed.stdout or "[]")
    ids = settled(comments, questions.registered_ids(target_dir, slug),
                  questions.answered(target_dir, slug))
    for comment_id in ids:
        result = run(["tuicr", "review", "delete", "--repo", target_dir,
                      "--session", slug, "--comment-id", comment_id])
        if not result.ok:
            raise SkillError(f"could not delete comment {comment_id}: {result.stderr.strip()}")
    questions.forget(target_dir, slug, set(ids))
    return ids


def report_path(target_dir: str, slug: str) -> Path:
    """Deterministic close-report path for one review session."""
    return paths.state_dir("tuicr", "reviews", create=True) / f"{paths.short_hash(target_dir, slug)}.json"


def persist_close_report(target_dir: str, slug: str, verdict: dict) -> Path:
    """Write the close report to its deterministic per-session path."""
    state_file = report_path(target_dir, slug)
    paths.write_json_atomic(state_file, verdict, indent=2)
    return state_file


def launch(target_dir: str) -> int:
    """Run tuicr in this terminal until it exits, then persist the close report."""
    remote = os.environ.get("TUICR_REMOTE", "origin")
    dirty = bool(git(target_dir, "status", "--porcelain").stdout.strip())
    revset = ""
    if not dirty:
        log_info(f"Refreshing review refs from '{remote}'")
        revset = resolve_revset(target_dir)

    log_info(f"Directory: {target_dir}")
    log_info("Review target: worktree" if dirty else f"Review target: {revset}")
    head_before = git(target_dir, "rev-parse", "HEAD").stdout.strip()

    # tuicr inherits this terminal so editors launched by its `e` binding can
    # take it over; capturing its output would hand nvim a pipe.
    command = ["tuicr", "-w"] if dirty else ["tuicr", "-r", revset]
    process = subprocess.Popen(command, cwd=target_dir)  # noqa: S603 - argument vector
    try:
        review_slug = active_review_slug(target_dir)
        # A report left by an earlier review of this session must not read as
        # closure while this one is still open.
        report_path(target_dir, review_slug).unlink(missing_ok=True)
        # Work queued in the previous round was applied before this one opened.
        workqueue.archive(
            workqueue.default_inbox("tuicr-feedback"), workqueue.scope(target_dir, review_slug))
        exit_code = process.wait()
    finally:
        if process.poll() is None:
            process.wait()
    log_info("tuicr finished")
    log_info("If you exported instructions, they are in your clipboard")

    verdict = review_verdict(target_dir, review_slug, head_before, exit_code)
    wiped = wipe_settled(target_dir, review_slug)
    log_info(f"Wiped {len(wiped)} settled comment(s); descriptions and review notes remain")
    report = persist_close_report(target_dir, review_slug, verdict)
    log_info(f"verdict={verdict['verdict']}; report={report}")
    return 0


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(prog="tuicr_up.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", nargs="?", default=".")
    args = parser.parse_args(argv)

    if which("tuicr") is None:
        log_error("tuicr not found. Install it first.")
        return 1
    warn_outdated()

    if not Path(args.directory).is_dir():
        log_error(f"Not a git repository: {args.directory}")
        return 1
    target_dir = str(Path(args.directory).resolve())

    if not git(target_dir, "rev-parse", "--git-dir").ok:
        log_error(f"Not a git repository: {target_dir}")
        return 1

    lock = review_lock(target_dir)
    if lock.live_owner() is not None:
        log_warn(f"tuicr is already reviewing {target_dir} (launcher PID {lock.live_owner()})")
        return 0

    with lock:
        return launch(target_dir)


if __name__ == "__main__":
    run_main(main)
