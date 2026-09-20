#!/usr/bin/env python3
"""Start, stop and list tuicr-watch daemons.

Deliberately NOT a tmux window: the watcher needs no UI and a window per
watched session clutters the human's workspace. Its PID is tracked in a pidfile
under the state dir so it can be stopped cleanly later.

This is generic daemon lifecycle management — it knows nothing about any
particular workflow, so an orchestrator, a solo coding agent, or a human can
all drive it the same way.

Contract::

    watch_up.py --repo <dir> --session <slug> --cli-session <uuid>
                [--cli-pane <pane>] [--name <id>] [--persistent]
                [--ignore-type <type>]... [--rearm <s>] [--max-attempts <n>]
                [--event-timeout <s>] [--queue-timeout <s>] [--state-dir <dir>]
    watch_up.py --stop <name> [--state-dir <dir>]
    watch_up.py --stop-all [--prefix <p>] [--exclude <substr>] [--state-dir <dir>]
    watch_up.py --list [--state-dir <dir>]

``--name`` is a caller-chosen label used for the pidfile, so a caller running
several watchers can retire exactly the one it means. ``--persistent`` only tags
the watcher so a bulk ``--stop-all`` sweep can exclude it; it changes no
behaviour.

Emits on start: ``WATCH_PID``, ``WATCH_PIDFILE``, ``WATCH_LOG``, ``WATCH_NAME``.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import paths  # noqa: E402
from skillkit.cli import run_main  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402
from skillkit.lock import PidFile  # noqa: E402
from skillkit.proc import command_of, detach, pid_alive, terminate  # noqa: E402

#: Substrings that identify a tuicr-watch process in its command line. Both
#: spellings are accepted so a daemon started by the pre-port shell
#: implementation is still recognised by this one.
_WATCHER_MARKERS = ("tuicr_watch", "tuicr-watch")


def is_watcher(pid: int) -> bool:
    """Whether *pid* still looks like a tuicr-watch daemon.

    Guards against PID reuse before signalling. When the command line cannot
    be read at all this returns False, so an unidentifiable process is never
    killed on the strength of a pidfile alone.
    """
    command = command_of(pid)
    return any(marker in command for marker in _WATCHER_MARKERS)

#: Characters kept in a pidfile name; everything else becomes '-'.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")

#: Seconds to wait before confirming a freshly started daemon is still alive.
_STARTUP_GRACE = 1.0

#: State locations used by earlier implementations. The shell version of this
#: tool recorded pidfiles under ``~/.local/state/tuicr-watch/``; the Python port
#: moved them to ``~/.local/state/tuicr/watch/``. A watcher is long-lived — one
#: observed here had run for 17 days — so daemons started before the move are
#: still alive long after it, and a lookup that only checks the current
#: directory silently reports success while leaving them running. Every
#: read/stop path therefore also scans these; nothing is ever written to them.
_LEGACY_STATE_DIRS = ("tuicr-watch",)


def search_dirs(state_dir: Path, *, include_legacy: bool) -> list[Path]:
    """Directories to scan when locating existing watchers.

    New state is always written to *state_dir*; the legacy directories are
    read-only fallbacks so a daemon started by an older version can still be
    listed and stopped.

    Args:
        state_dir: The current, authoritative state directory.
        include_legacy: False when the caller named an explicit ``--state-dir``.
            An explicit directory means isolation (tests, or a caller managing
            a private set), and reaching outside it would let such a caller
            stop unrelated real watchers.

    Returns:
        list[Path]: *state_dir* first, then any existing legacy directory.
    """
    dirs = [state_dir]
    if include_legacy:
        for name in _LEGACY_STATE_DIRS:
            legacy = paths.state_dir(name)
            if legacy.is_dir() and legacy.resolve() != state_dir.resolve():
                dirs.append(legacy)
    return dirs


def sanitize(name: str) -> str:
    """Reduce *name* to characters safe in a filename."""
    return _SAFE_NAME_RE.sub("-", name)


def _retire_pidfile(pidfile: Path, *, announce: bool) -> None:
    """Stop the daemon recorded in *pidfile* and clear its state files.

    A pidfile records a PID, not an identity, and the kernel recycles PIDs. A
    stale pidfile — left by a daemon that died without cleaning up — can
    therefore name a PID that now belongs to an unrelated process, and killing
    it would terminate innocent work. The signal is gated on the target still
    looking like a watcher; a mismatch clears the stale pidfile but sends
    nothing.
    """
    if not pidfile.is_file():
        return
    raw = pidfile.read_text(encoding="utf-8", errors="replace").strip()
    if raw.isdigit():
        pid = int(raw)
        if pid_alive(pid) and not is_watcher(pid):
            print(
                f"SKIPPED={pidfile} (pid {pid} is not a tuicr-watch process; "
                "stale pidfile cleared without signalling)",
                file=sys.stderr,
            )
        else:
            terminate(pid)
    pidfile.unlink(missing_ok=True)
    pidfile.with_suffix(".persistent").unlink(missing_ok=True)
    if announce:
        print(f"STOPPED={pidfile}")


def stop_pidfile(pidfile: Path) -> None:
    """Stop the daemon recorded in *pidfile* and clear its state files."""
    _retire_pidfile(pidfile, announce=True)


def list_watchers(state_dirs: list[Path]) -> int:
    """Print one line per known watcher: name, pid, liveness and persistence.

    A watcher found outside the first (current) directory is tagged ``legacy``
    so the human can see that it predates the current state layout.
    """
    for index, state_dir in enumerate(state_dirs):
        for pidfile in sorted(state_dir.glob("*.pid")):
            raw = pidfile.read_text(encoding="utf-8", errors="replace").strip()
            alive = raw.isdigit() and pid_alive(int(raw))
            tag = " persistent" if pidfile.with_suffix(".persistent").exists() else ""
            tag += " legacy" if index else ""
            print(f"{pidfile.stem} pid={raw or '?'} {'live' if alive else 'dead'}{tag}")
    return 0


def stop_all(state_dirs: list[Path], prefix: str, exclude: str) -> int:
    """Stop every watcher matching *prefix*, skipping names containing *exclude*."""
    for state_dir in state_dirs:
        for pidfile in sorted(state_dir.glob(f"{prefix}*.pid")):
            if exclude and exclude in pidfile.name:
                continue
            stop_pidfile(pidfile)
    return 0


def _pidfiles_for_pid(state_dirs: list[Path], pid: int) -> list[Path]:
    """Every watcher pidfile in *state_dirs* that still names *pid*."""
    matches: list[Path] = []
    for state_dir in state_dirs:
        for pidfile in sorted(state_dir.glob("*.pid")):
            raw = pidfile.read_text(encoding="utf-8", errors="replace").strip()
            if raw.isdigit() and int(raw) == pid:
                matches.append(pidfile)
    return matches


def start(args: argparse.Namespace, state_dir: Path) -> int:
    """Start a watcher daemon, replacing any live one for this name.

    Raises:
        SkillError: Exit 2 for a missing required option, 3 for a missing
            watcher or repo, 4 if the daemon died during startup.
    """
    for flag, value in (("--repo", args.repo), ("--session", args.session), ("--cli-session", args.cli_session)):
        if not value:
            raise SkillError(f"{flag} is required", code=2)

    watcher = Path(__file__).resolve().parent / "tuicr_watch.py"
    if not watcher.is_file():
        raise SkillError(f"tuicr-watch not found at {watcher}", code=3)
    if not Path(args.repo).is_dir():
        raise SkillError(f"repo '{args.repo}' does not exist", code=3)

    lookup = search_dirs(state_dir, include_legacy=not args.state_dir)
    name = sanitize(args.name or args.session)
    pidfile = state_dir / f"{name}.pid"
    logfile = state_dir / f"{name}.log"

    lock_key = paths.short_hash(str(Path(args.repo).resolve()), args.session)
    lock = PidFile(state_dir / f"{lock_key}.lock")
    owner = lock.live_owner()
    if owner is not None:
        if not lock.stop_owner():
            raise SkillError(
                f"another process (PID {owner}) already holds {lock.path.name}.",
                code=8,
                hint="Stop it first, or use the watch-up helper which reuses a live daemon.",
            )
        for candidate in _pidfiles_for_pid(lookup, owner):
            candidate.unlink(missing_ok=True)
            candidate.with_suffix(".persistent").unlink(missing_ok=True)

    # Replace any existing watcher for this (repo, session) pair before
    # starting the fresh daemon that should own the session now.
    if pidfile.is_file():
        _retire_pidfile(pidfile, announce=False)

    command = [
        sys.executable, str(watcher),
        "--repo", args.repo,
        "--session", args.session,
        "--cli-session", args.cli_session,
        "--state-dir", str(state_dir),
    ]
    for flag, value in (
        ("--cli-pane", args.cli_pane),
        ("--event-timeout", args.event_timeout),
        ("--queue-timeout", args.queue_timeout),
        ("--rearm", args.rearm),
        ("--max-attempts", args.max_attempts),
    ):
        if value:
            command += [flag, str(value)]
    # Any --ignore-type given REPLACES the daemon's default `reply`-only ignore
    # set, so a caller that also wants replies ignored must pass it explicitly.
    for ignore_type in args.ignore_type:
        command += ["--ignore-type", ignore_type]

    pid = detach(command, logfile)
    pidfile.write_text(f"{pid}\n", encoding="utf-8")
    if args.persistent:
        (state_dir / f"{name}.persistent").touch()

    time.sleep(_STARTUP_GRACE)
    if not pid_alive(pid):
        head = ""
        try:
            head = "\n".join(logfile.read_text(encoding="utf-8", errors="replace").splitlines()[:20])
        except OSError:
            pass
        pidfile.unlink(missing_ok=True)
        if head:
            print(head, file=sys.stderr)
        raise SkillError(f"watch daemon failed to start; see {logfile}", code=4)

    print(f"WATCH_PID={pid}")
    print(f"WATCH_PIDFILE={pidfile}")
    print(f"WATCH_LOG={logfile}")
    print(f"WATCH_NAME={name}")
    print(f"WATCH_PERSISTENT={1 if args.persistent else 0}")
    return 0


def _value_flags(parser: argparse.ArgumentParser) -> frozenset[str]:
    """Every option of *parser* that consumes a following value.

    Derived from the parser rather than hand-listed so it cannot drift out of
    sync when a flag is added. Flag-style actions (``store_true``, ``--help``)
    report ``nargs == 0``; value-taking ones report ``None`` (exactly one).
    """
    return frozenset(
        option
        for action in parser._actions
        if action.nargs != 0
        for option in action.option_strings
    )


def _fold_dash_values(argv: list[str], value_flags: frozenset[str]) -> list[str]:
    """Fold ``--flag VALUE`` into ``--flag=VALUE`` when VALUE begins with ``-``.

    argparse rejects a value that begins with ``-`` because it looks like an
    option, but callers legitimately pass values such as ``--exclude -ado-``.
    The ``--flag=VALUE`` form is always accepted, so folding the pair restores
    the shell version's behaviour without loosening parsing anywhere else.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if (
            tok in value_flags
            and i + 1 < len(argv)
            and argv[i + 1].startswith("-")
            and argv[i + 1] != "--"
        ):
            out.append(f"{tok}={argv[i + 1]}")
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(prog="watch_up.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default="")
    parser.add_argument("--session", default="")
    parser.add_argument("--cli-session", default="")
    parser.add_argument("--cli-pane", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--persistent", action="store_true")
    parser.add_argument("--ignore-type", action="append", default=[])
    parser.add_argument("--rearm", default="")
    parser.add_argument("--max-attempts", default="")
    parser.add_argument("--event-timeout", default="")
    parser.add_argument("--queue-timeout", default="")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--stop", default="")
    parser.add_argument("--stop-all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--exclude", default="")

    known, extra = parser.parse_known_args(_fold_dash_values(argv, _value_flags(parser)))
    if extra:
        raise SkillError(f"unknown arg '{extra[0]}'", code=2)
    return known


def main(argv: list[str]) -> int:
    """Entry point."""
    args = parse_args(argv)
    state_dir = Path(args.state_dir) if args.state_dir else paths.state_dir("tuicr", "watch")
    state_dir.mkdir(parents=True, exist_ok=True)
    lookup = search_dirs(state_dir, include_legacy=not args.state_dir)

    if args.list:
        return list_watchers(lookup)
    if args.stop:
        name = sanitize(args.stop)
        for candidate in lookup:
            pidfile = candidate / f"{name}.pid"
            if pidfile.is_file():
                stop_pidfile(pidfile)
                break
        return 0
    if args.stop_all:
        return stop_all(lookup, args.prefix, args.exclude)
    return start(args, state_dir)


if __name__ == "__main__":
    run_main(main)
