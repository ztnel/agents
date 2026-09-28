#!/usr/bin/env python3
"""Launch tuicr in a new tmux window to review git changes.

Refreshes the review refs (so the diff reflects the remote, not a stale local
copy), opens a detached tmux window running tuicr over the resolved revset,
starts the review watch daemon for the current Copilot CLI session, switches
focus to the review window, and blocks until tuicr exits — then surfaces
whatever tuicr exported.

Usage::

    tuicr_up.py [directory]

Arguments:
    directory: Git repository to review. Default: current directory.

Environment variables:
    TUICR_WINDOW_NAME: Name of the new tmux window. Default: ``tuicr``.
    TUICR_BASE_REF: Base ref for the review revset. Default: the remote's
        default branch — never a hardcoded name, so this works in any repo.
    TUICR_HEAD_REF: Head ref for the review revset. Default: ``HEAD``.
    TUICR_REMOTE: Remote refreshed before a branch review. Default: ``origin``.
"""

from __future__ import annotations

import os
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import paths  # noqa: E402
from skillkit.copilot import session_for_pane_pid  # noqa: E402
from skillkit.cli import parse_metadata, run_main  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402
from skillkit.gitio import git  # noqa: E402
from skillkit.proc import run, which  # noqa: E402
from skillkit.tmuxio import inside_tmux, pane_exists, pane_pid  # noqa: E402
from tuicr_watch import WatchConfig, WakeDeliverer, resolve_target  # noqa: E402

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


def tuicr_supports_stdout() -> bool:
    """Whether this tuicr accepts ``--stdout``.

    Older builds export to the clipboard instead, which cannot be captured, so
    the caller has to fall back to asking the human to paste.
    """
    result = run(["tuicr", "--help"], merge_stderr=True)
    return "--stdout" in result.stdout


def already_reviewing(directory: str) -> bool:
    """Whether a tuicr pane is already open on *directory*.

    Scoped to this checkout deliberately: a global check would block reviewing
    a second repository at the same time.
    """
    result = run(["tmux", "list-panes", "-a", "-F", "#{pane_current_command} #{pane_current_path}"])
    return result.ok and f"tuicr {directory}" in result.lines()


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


def resolve_cli_session() -> str:
    """Resolve the Copilot CLI session bound to the current tmux pane."""
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        raise SkillError("could not resolve the current tmux pane")
    pid = pane_pid(pane)
    if pid is None:
        raise SkillError(f"could not resolve the pane PID for {pane}")
    return session_for_pane_pid(pid).session_id


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
    comments_result = run([
        sys.executable, str(helper),
        "--repo", target_dir,
        "--session", slug,
        "comments", "--unanswered", "--json",
    ])
    try:
        unanswered = json.loads(comments_result.stdout) if comments_result.ok else None
    except json.JSONDecodeError:
        unanswered = None
    head_result = git(target_dir, "rev-parse", "HEAD")
    head_after = head_result.stdout.strip() if head_result.ok else ""

    reasons = []
    if exit_code != 0:
        reasons.append("tuicr did not exit cleanly")
    if not head_after or head_after != head_before:
        reasons.append("HEAD changed during review")
    if not marks.get("ok"):
        reasons.append("not every changed file is reviewed at current content")
    approved = not reasons
    return {
        "approved": approved,
        "verdict": "approved" if approved else ("aborted" if exit_code != 0 else "incomplete"),
        "repo": target_dir,
        "session": slug,
        "head_before": head_before,
        "head_after": head_after,
        "tuicr_exit_code": exit_code,
        "marks": marks,
        "unanswered": unanswered,
        "reasons": reasons,
    }


def wake_closed_review(target_dir: str, slug: str, cli_session: str, verdict: dict) -> None:
    """Wake the spawning CLI with the persisted close verdict."""
    state_dir = paths.state_dir("tuicr", "reviews", create=True)
    state_file = state_dir / f"{paths.short_hash(target_dir, slug)}.json"
    paths.write_json_atomic(state_file, verdict, indent=2)
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


def launch(target_dir: str) -> int:
    """Open tuicr in a tmux window and block until it exits."""
    window_name = os.environ.get("TUICR_WINDOW_NAME", "tuicr")
    remote = os.environ.get("TUICR_REMOTE", "origin")
    cli_session = resolve_cli_session()
    watch_name = ""

    dirty = bool(git(target_dir, "status", "--porcelain").stdout.strip())
    revset = ""
    if not dirty:
        log_info(f"Refreshing review refs from '{remote}'")
        revset = resolve_revset(target_dir)

    if already_reviewing(target_dir):
        log_warn(f"tuicr is already reviewing {target_dir} in another window")
        log_info("Switch to it with Ctrl-b + w (window list)")
        review_slug = active_review_slug(target_dir)
        watch_name = start_watch(target_dir, review_slug, cli_session)
        return 0

    log_info(f"Launching tuicr in a new tmux window ('{window_name}')")
    log_info(f"Directory: {target_dir}")
    if dirty:
        log_info("Review target: worktree")
    else:
        log_info(f"Review target: {revset}")

    head_before = git(target_dir, "rev-parse", "HEAD").stdout.strip()

    output_file = ""
    if tuicr_supports_stdout():
        handle, output_file = tempfile.mkstemp(prefix="tuicr-output.", dir=os.environ.get("TMPDIR", "/tmp"))
        os.close(handle)
        inner = f"tuicr {'-w' if dirty else '-r ' + _sh_quote(revset)} --stdout > {_sh_quote(output_file)}"
        log_info("Using --stdout mode (output will be captured)")
    else:
        inner = "tuicr -w" if dirty else f"tuicr -r {_sh_quote(revset)}"
        log_warn("tuicr --stdout not supported, output will be copied to clipboard")

    handle, completion_file = tempfile.mkstemp(
        prefix="tuicr-complete.", dir=os.environ.get("TMPDIR", "/tmp")
    )
    os.close(handle)
    Path(completion_file).unlink(missing_ok=True)
    command = (
        f"cd {_sh_quote(target_dir)} && {inner}; "
        f"rc=$?; printf '%s\\n' \"$rc\" > {_sh_quote(completion_file)}"
    )
    created = run(
        ["tmux", "new-window", "-d", "-P", "-F", "#{pane_id}", "-n", window_name, "-c", target_dir, command]
    )
    if not created.ok:
        raise SkillError(f"could not create tmux window: {created.stderr}")
    pane_id = created.stdout.strip()

    run(["tmux", "select-window", "-t", pane_id])
    review_slug = active_review_slug(target_dir)
    watch_name = start_watch(target_dir, review_slug, cli_session)
    log_info(f"tuicr is running in window '{window_name}' (pane {pane_id})")
    log_info("Waiting for tuicr to exit...")
    try:
        while pane_exists(pane_id):
            time.sleep(0.2)
        log_info("tuicr finished")
    finally:
        if watch_name:
            stop_watch(watch_name)

    exit_code = None
    try:
        raw_exit = Path(completion_file).read_text(encoding="utf-8").strip()
        exit_code = int(raw_exit)
    except (OSError, ValueError):
        pass
    finally:
        Path(completion_file).unlink(missing_ok=True)

    verdict = review_verdict(target_dir, review_slug, head_before, exit_code)
    wake_closed_review(target_dir, verdict["session"], cli_session, verdict)

    if output_file and Path(output_file).is_file():
        content = Path(output_file).read_text(encoding="utf-8", errors="replace")
        if content.strip():
            print()
            print("=== TUICR INSTRUCTIONS ===")
            print(content, end="" if content.endswith("\n") else "\n")
            print("=== END TUICR INSTRUCTIONS ===")
        else:
            log_info("No instructions exported from tuicr")
            log_info("If you exported to clipboard, paste the instructions here")
        Path(output_file).unlink(missing_ok=True)
    else:
        log_info("If you exported instructions, they are in your clipboard - paste them here")
    return 0


def _sh_quote(value: str) -> str:
    """Quote *value* for the shell command string tmux runs.

    tmux's ``new-window`` takes a command *string*, not an argument vector, so
    this one interface genuinely needs quoting.
    """
    import shlex

    return shlex.quote(value)


def main(argv: list[str]) -> int:
    """Entry point."""
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    if which("tuicr") is None:
        log_error("tuicr not found. Install it first.")
        return 1

    target = argv[0] if argv else "."
    if not Path(target).is_dir():
        log_error(f"Not a git repository: {target}")
        return 1
    target_dir = str(Path(target).resolve())

    if not git(target_dir, "rev-parse", "--git-dir").ok:
        log_error(f"Not a git repository: {target_dir}")
        return 1

    if not inside_tmux():
        log_error("Not running inside tmux!")
        print()
        print("To use tuicr with your coding agent, run that agent inside tmux.")
        print()
        print("1. Exit the current agent session.")
        print()
        print("2. Restart the agent inside tmux.")
        print()
        print("3. Then run /tuicr again.")
        return 1

    if already_reviewing(target_dir):
        log_warn(f"tuicr is already reviewing {target_dir} in another window")
        log_info("Switch to it with Ctrl-b + w (window list)")
        return 0

    return launch(target_dir)


if __name__ == "__main__":
    run_main(main)
