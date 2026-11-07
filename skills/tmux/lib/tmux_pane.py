#!/usr/bin/env python3
"""Discover, open, verify, focus, resize, inspect and close tmux panes.

Every command prints one JSON object on stdout. Mutating commands accept only
exact pane IDs (``%12``) so they can never touch a pane the caller did not name.

Usage::

    tmux_pane.py status
    tmux_pane.py list [--scope all|session|window] [--target T]
    tmux_pane.py open --cwd DIR [--window NAME | --split h|v [--size S]]
                      [--target PANE] [--env NAME]... [--expect CMD]
                      [--timeout S] [--settle S] [--focus] -- COMMAND [ARG]...
    tmux_pane.py focus --pane PANE
    tmux_pane.py resize --pane PANE [--width W] [--height H]
    tmux_pane.py capture --pane PANE [--lines N]
    tmux_pane.py close --pane PANE [--allow-self]

Exit codes: 0 ok, 2 usage, 3 tmux missing or no tmux session, 4 tmux refused
or the pane is gone, 5 the opened pane did not become ready.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import tmuxio  # noqa: E402
from skillkit.cli import run_main  # noqa: E402
from skillkit.errors import MissingDependency, SkillError, UsageError  # noqa: E402
from skillkit.proc import descendant_commands  # noqa: E402

_POLL = 0.1


def _emit(value: dict) -> int:
    print(json.dumps(value, sort_keys=True))
    return 0


def _status() -> dict:
    available = tmuxio.available()
    inside = available and tmuxio.inside_tmux()
    return {
        "available": available,
        "inside": inside,
        "pane": os.environ.get("TMUX_PANE", "") if inside else "",
        "version": tmuxio.version() if available else "",
    }


def _require_session() -> dict:
    status = _status()
    if not status["available"]:
        raise MissingDependency("tmux is not installed.", hint="Launch the command in another terminal instead.")
    if not status["inside"]:
        raise MissingDependency("not inside a tmux session ($TMUX unset).", hint="Launch the command in another terminal instead.")
    return status


def _session_of(pane: str) -> str:
    described = tmuxio.describe_pane(pane)
    if not described:
        raise SkillError(f"pane {pane} does not exist.", code=4)
    return described["session_id"]


def _wait_ready(pane: str, expect: str | None, timeout: float, settle: float) -> dict:
    """Wait until *pane* runs *expect* (if set) and is still alive after *settle*."""
    start = time.monotonic()
    deadline = start + max(timeout, settle)
    while True:
        described = tmuxio.describe_pane(pane)
        if not described:
            raise SkillError(f"pane {pane} exited before it became ready.", code=5)
        pid = described["pane_pid"]
        started = not expect or (bool(pid) and expect in descendant_commands(pid))
        if started and time.monotonic() - start >= settle:
            return described
        if time.monotonic() >= deadline:
            raise SkillError(
                f"pane {pane} is running but {expect!r} did not start within {timeout:g}s.",
                code=5,
                hint=f"Inspect it with: tmux_pane.py capture --pane {pane}",
            )
        time.sleep(_POLL)


def _cmd_open(args: argparse.Namespace) -> int:
    status = _require_session()
    if not args.command:
        raise UsageError("open needs a command after --")
    if not Path(args.cwd).is_dir():
        raise UsageError(f"--cwd is not a directory: {args.cwd}")
    origin = args.target or status["pane"]
    if args.split:
        target = origin
    else:
        target = _session_of(tmuxio.require_pane_id(origin))
    env = {name: os.environ[name] for name in args.env if name in os.environ}
    pane = tmuxio.open_pane(
        shlex.join(args.command),
        cwd=str(Path(args.cwd).resolve()),
        target=target,
        window_name=args.window,
        split=args.split,
        size=args.size,
        env=env,
    )
    described = _wait_ready(pane, args.expect, args.timeout, args.settle)
    if args.focus:
        tmuxio.focus_pane(pane)
    return _emit({"ready": True, **described})


def _cmd_list(args: argparse.Namespace) -> int:
    _require_session()
    return _emit({"panes": tmuxio.describe_panes(args.target, scope=args.scope)})


def _existing(pane: str) -> dict:
    described = tmuxio.describe_pane(pane)
    if not described:
        raise SkillError(f"pane {pane} does not exist.", code=4)
    return described


def _cmd_focus(args: argparse.Namespace) -> int:
    _require_session()
    _existing(args.pane)
    tmuxio.focus_pane(args.pane)
    return _emit(_existing(args.pane))


def _cmd_resize(args: argparse.Namespace) -> int:
    _require_session()
    _existing(args.pane)
    tmuxio.resize_pane(args.pane, width=args.width, height=args.height)
    return _emit(_existing(args.pane))


def _cmd_capture(args: argparse.Namespace) -> int:
    _require_session()
    _existing(args.pane)
    return _emit({"pane_id": args.pane, "text": tmuxio.capture_lines(args.pane, args.lines)})


def _cmd_close(args: argparse.Namespace) -> int:
    status = _require_session()
    _existing(args.pane)
    if args.pane == status["pane"] and not args.allow_self:
        raise UsageError(f"refusing to close the calling pane {args.pane}", hint="Pass --allow-self to override.")
    tmuxio.close_pane(args.pane)
    return _emit({"closed": args.pane})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tmux_pane.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("status", help="Report tmux availability and the calling pane.")

    listing = sub.add_parser("list", help="Describe panes.")
    listing.add_argument("--scope", choices=("all", "session", "window"), default="all")
    listing.add_argument("--target", help="Session, window or pane the scope is relative to.")

    opening = sub.add_parser("open", help="Run a command in a new detached pane and verify it started.")
    opening.add_argument("--cwd", required=True)
    layout = opening.add_mutually_exclusive_group()
    layout.add_argument("--window", help="Name for a new window (default layout).")
    layout.add_argument("--split", choices=("h", "v"), help="Split --target instead of opening a window.")
    opening.add_argument("--size", help="Split size, N or N%%.")
    opening.add_argument("--target", help="Pane to split, or whose session gets the window. Default: calling pane.")
    opening.add_argument("--env", action="append", default=[], metavar="NAME", help="Copy NAME from this environment.")
    opening.add_argument("--expect", help="Process name that must appear in the pane's process tree.")
    opening.add_argument("--timeout", type=float, default=10.0)
    opening.add_argument("--settle", type=float, default=0.3, help="Seconds the pane must stay alive.")
    opening.add_argument("--focus", action="store_true", help="Focus the pane once it is ready.")
    opening.add_argument("command", nargs=argparse.REMAINDER)

    for name, text in (("focus", "Focus a pane."), ("resize", "Resize a pane."),
                       ("capture", "Print a pane's text."), ("close", "Close a pane.")):
        action = sub.add_parser(name, help=text)
        action.add_argument("--pane", required=True, type=_pane_id)
        if name == "resize":
            action.add_argument("--width")
            action.add_argument("--height")
        if name == "capture":
            action.add_argument("--lines", type=int)
        if name == "close":
            action.add_argument("--allow-self", action="store_true")
    return parser


def _pane_id(value: str) -> str:
    if not tmuxio.PANE_ID_RE.match(value):
        raise argparse.ArgumentTypeError(f"expected an exact pane id like %12, got {value!r}")
    return value


def main(argv: Sequence[str]) -> int:
    args = _parser().parse_args(list(argv))
    if args.action == "open" and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if args.action == "status":
        return _emit(_status())
    handlers = {
        "open": _cmd_open, "list": _cmd_list, "focus": _cmd_focus,
        "resize": _cmd_resize, "capture": _cmd_capture, "close": _cmd_close,
    }
    return handlers[args.action](args)


if __name__ == "__main__":
    run_main(main)
