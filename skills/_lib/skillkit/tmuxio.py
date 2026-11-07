"""tmux control: panes and windows for the review-window launcher.

tmux has no native Windows port, which is what scopes these skills to Linux,
macOS and WSL. Agent feedback travels over MCP, never through synthetic keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .proc import run


@dataclass(frozen=True)
class Pane:
    """A tmux pane and the PID of the process running in it."""

    pane_id: str
    pane_pid: int


def available() -> bool:
    """Whether a tmux binary is on PATH."""
    from .proc import which

    return which("tmux") is not None


def inside_tmux() -> bool:
    """Whether this process is running inside a tmux session (``$TMUX`` set)."""
    return bool(os.environ.get("TMUX"))


def version() -> str:
    """Output of ``tmux -V``, or ``"unknown"``."""
    result = run(["tmux", "-V"])
    return result.stdout.strip() if result.ok else "unknown"


def display(target: str, fmt: str) -> str | None:
    """Evaluate a tmux format string against *target*, or None if it is gone."""
    result = run(["tmux", "display-message", "-p", "-t", target, fmt])
    return result.stdout.strip() if result.ok else None


def pane_exists(pane: str) -> bool:
    """Whether *pane* resolves to a live pane."""
    return bool(display(pane, "#{pane_id}"))


def pane_pid(pane: str) -> int | None:
    """PID of the process running in *pane*, or None."""
    value = display(pane, "#{pane_pid}")
    return int(value) if value and value.isdigit() else None


def window_is_active(pane: str) -> bool:
    """Whether *pane*'s window is the active one in its session."""
    return display(pane, "#{window_active}") == "1"


def list_panes() -> list[Pane]:
    """Every pane in every session, with its PID."""
    result = run(["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"])
    if not result.ok:
        return []
    panes = []
    for line in result.lines():
        pane_id, _, pid = line.partition(" ")
        if pane_id and pid.strip().isdigit():
            panes.append(Pane(pane_id=pane_id, pane_pid=int(pid.strip())))
    return panes


def capture(pane: str) -> str | None:
    """Visible text of *pane*, or None if it cannot be captured.

    Only ever a diagnostic. Terminal rendering is never the oracle for whether
    input was accepted.
    """
    result = run(["tmux", "capture-pane", "-p", "-t", pane])
    return result.stdout if result.ok else None


def window_exists(name: str) -> bool:
    """Whether a window named *name* exists in any session."""
    result = run(["tmux", "list-windows", "-a", "-F", "#{window_name}"])
    return result.ok and name in result.lines()


def new_window(name: str, command: str, *, cwd: str | None = None, detached: bool = True) -> bool:
    """Create a tmux window named *name* running *command*."""
    args = ["tmux", "new-window"]
    if detached:
        args.append("-d")
    if cwd:
        args += ["-c", cwd]
    args += ["-n", name, command]
    return run(args).ok


def kill_window(name: str) -> bool:
    """Kill the window named *name*."""
    return run(["tmux", "kill-window", "-t", name]).ok


def rename_window(target: str, name: str) -> bool:
    """Rename window *target* to *name*."""
    return run(["tmux", "rename-window", "-t", target, name]).ok


def rename_session(target: str, name: str) -> bool:
    """Rename session *target* to *name*."""
    return run(["tmux", "rename-session", "-t", target, name]).ok
