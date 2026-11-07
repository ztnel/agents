#!/usr/bin/env python3
"""Run a tuicr review in the current terminal and report how it closed.

Refreshes the review refs (so the diff reflects the remote, not a stale local
copy), runs tuicr in the foreground over the resolved revset, starts the review
watch daemon for the given Copilot CLI session, and, after tuicr exits,
persists the deterministic close report and wakes that session.

This script never drives a terminal multiplexer. Run it wherever the human will
review: the caller's agent opens a pane for it (for example through the tmux
skill), or the human runs it in a terminal of their own.

Usage::

    tuicr_up.py [--cli-session UUID] [directory]

Arguments:
    directory: Git repository to review. Default: current directory.
    --cli-session: Copilot CLI session to watch for and wake on close. Without
        it the review still runs and the close report is persisted, but no
        watcher starts and no wake is sent.

Environment variables:
    TUICR_BASE_REF: Base ref for the review revset. Default: the remote's
        default branch — never a hardcoded name, so this works in any repo.
    TUICR_HEAD_REF: Head ref for the review revset. Default: ``HEAD``.
    TUICR_REMOTE: Remote refreshed before a branch review. Default: ``origin``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import paths  # noqa: E402
from skillkit.approval import ApprovalReceipt, ReviewedFile  # noqa: E402
from skillkit.cli import parse_metadata, run_main  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402
from skillkit.gitio import git  # noqa: E402
from skillkit.lock import PidFile  # noqa: E402
from skillkit.proc import run, which  # noqa: E402
from tuicr_watch import WatchConfig, WakeDeliverer, resolve_target  # noqa: E402
import questions  # noqa: E402

#: ANSI colours for the `[tuicr]` log prefix. Suppressed when stdout is not a
#: TTY so captured output stays clean.
_GREEN = "\033[0;32m"
_YELLOW = "\033[1;33m"
_RED = "\033[0;31m"
_RESET = "\033[0m"
_WATCH_RETRY_DELAY = 0.1
_WATCH_RETRY_LIMIT = 20


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


def active_review_slug(target_dir: str) -> str:
    """Resolve the single active tuicr review session for *target_dir*.

    A new review window is expected to make exactly one session active. If the
    session has not settled yet, the helper retries briefly before failing so
    the launcher does not race the TUI startup.
    """
    for attempt in range(_WATCH_RETRY_LIMIT):
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
        if attempt + 1 < _WATCH_RETRY_LIMIT:
            time.sleep(_WATCH_RETRY_DELAY)
    raise SkillError(f"could not resolve an active tuicr session for {target_dir}")


def start_watch(target_dir: str, session_slug: str, cli_session: str) -> str:
    """Start the watch daemon for *session_slug* and return its pidfile label."""
    watcher = Path(__file__).resolve().parent / "watch_up.py"
    if not watcher.is_file():
        raise SkillError(f"Watch helper not found: {watcher}")

    name = f"tuicr-{paths.short_hash(target_dir, session_slug, cli_session)}"
    args = [
        sys.executable,
        str(watcher),
        "--repo", target_dir,
        "--session", session_slug,
        "--cli-session", cli_session,
        "--name", name,
    ]
    result = run(args)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if not result.ok:
        raise SkillError(f"could not start watch daemon: {result.stderr or result.stdout}", code=result.returncode)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    return name


def stop_watch(name: str) -> None:
    """Stop the watch daemon started under *name*."""
    watcher = Path(__file__).resolve().parent / "watch_up.py"
    run([sys.executable, str(watcher), "--stop", name])


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
    approved = not reasons
    return {
        "approved": approved,
        "verdict": "approved" if approved else ("aborted" if exit_code != 0 else "incomplete"),
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
        "reasons": reasons,
    }


def persist_close_report(target_dir: str, slug: str, verdict: dict) -> Path:
    """Write the close report to its deterministic per-session path."""
    state_dir = paths.state_dir("tuicr", "reviews", create=True)
    state_file = state_dir / f"{paths.short_hash(target_dir, slug)}.json"
    paths.write_json_atomic(state_file, verdict, indent=2)
    return state_file


def wake_closed_review(target_dir: str, slug: str, cli_session: str, verdict: dict) -> None:
    """Wake the spawning CLI with the persisted close verdict."""
    state_file = persist_close_report(target_dir, slug, verdict)
    report_identity = json.dumps(verdict, sort_keys=True, separators=(",", ":"))
    token = "tuicr-close-" + paths.short_hash(slug, report_identity)
    prompt = (
        f"tuicr close {token}: review session {slug} closed with "
        f"verdict={verdict['verdict']} ({target_dir}); report={state_file}. "
        "Follow the tuicr Close contract."
    )
    config = WatchConfig(repo=target_dir, session=slug, cli_session=cli_session)
    session, pane = resolve_target(config)
    deliverer = WakeDeliverer(config, session, pane, target_dir)
    try:
        if not deliverer.deliver_prompt(prompt, token, f"closed session {slug}"):
            raise SkillError("could not deliver review-close wake")
    finally:
        deliverer.close()


def start_watch_quietly(target_dir: str, slug: str, cli_session: str) -> tuple[str, str]:
    """Start the watcher without writing over the running TUI.

    Returns the watch name ("" if it could not start) and its captured output.
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            return start_watch(target_dir, slug, cli_session), captured.getvalue()
        except SkillError as exc:
            return "", f"{captured.getvalue()}watcher not started: {exc}\n"


def launch(target_dir: str, cli_session: str) -> int:
    """Run tuicr in this terminal until it exits, then report the close."""
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
    watch_name, watch_output = "", ""
    try:
        review_slug = active_review_slug(target_dir)
        if cli_session:
            watch_name, watch_output = start_watch_quietly(target_dir, review_slug, cli_session)
        exit_code = process.wait()
    finally:
        if process.poll() is None:
            process.wait()
        if watch_name:
            stop_watch(watch_name)
    if watch_output:
        print(watch_output, end="" if watch_output.endswith("\n") else "\n")
    log_info("tuicr finished")
    log_info("If you exported instructions, they are in your clipboard")

    verdict = review_verdict(target_dir, review_slug, head_before, exit_code)
    if not cli_session:
        report = persist_close_report(target_dir, review_slug, verdict)
        log_info(f"Close report: {report} (verdict={verdict['verdict']}; no CLI session to wake)")
        return 0
    try:
        wake_closed_review(target_dir, review_slug, cli_session, verdict)
    except SkillError as exc:
        report = persist_close_report(target_dir, review_slug, verdict)
        log_warn(f"Could not wake the CLI session ({exc}); close report: {report}")
    return 0


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(prog="tuicr_up.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--cli-session", default="", help="Copilot CLI session to watch for and wake.")
    parser.add_argument("directory", nargs="?", default=".")
    args = parser.parse_args(argv)

    if which("tuicr") is None:
        log_error("tuicr not found. Install it first.")
        return 1

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
        if args.cli_session:
            start_watch(target_dir, active_review_slug(target_dir), args.cli_session)
        return 0

    with lock:
        return launch(target_dir, args.cli_session)


if __name__ == "__main__":
    run_main(main)
