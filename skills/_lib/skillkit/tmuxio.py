"""tmux control: panes, windows, buffers and synthetic key delivery.

tmux is an assumed hard dependency — it is the transport that carries a wake
into a running Copilot CLI pane, and it has no native Windows port, which is
what scopes these skills to Linux, macOS and WSL.

The delicate part is delivering a prompt to an interactive TUI. Typing it with
``send-keys`` is wrong: the CLI's input box interprets keystrokes, so long text
arrives mangled and any newline submits early. Instead the text is loaded into
a tmux *buffer* and pasted as a single bracketed block, then submitted with one
explicit key. See :func:`paste` and :func:`submit_enter`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .errors import MissingDependency, SkillError
from .proc import require, run

#: ``ESC [ I`` — terminal focus-in report. With ``focus-events`` enabled the CLI
#: ignores synthetic submits while it believes its window is unfocused, so this
#: is injected to restore logical focus without stealing the human's window.
FOCUS_IN = ("1b", "5b", "49")

#: ``ESC [ O`` — terminal focus-out report, restoring the prior state.
FOCUS_OUT = ("1b", "5b", "4f")

#: ``ESC [ 1 3 u`` — Enter in the CSI-u (Kitty) keyboard encoding, which is how
#: the CLI distinguishes submit from a literal newline. A named ``Enter`` is the
#: only fallback tmux can express if this encoding is unavailable.
CSI_U_ENTER = ("1b", "5b", "31", "33", "75")


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


def require_capabilities() -> None:
    """Assert this tmux can do everything the wake transport needs.

    Checks for ``send-keys -H`` (hex key literals), which encodes the CSI-u
    submit sequence. Failing here is far cheaper to diagnose than failing
    obscurely half way through an injection on an old tmux.

    Raises:
        MissingDependency: If tmux is absent or too old.
    """
    require("tmux")
    result = run(["tmux", "list-commands", "send-keys"])
    if not result.ok or not result.stdout.strip():
        raise MissingDependency(
            "this tmux does not support 'list-commands send-keys'; upgrade tmux."
        )
    # tmux prints bundled flags, e.g. "send-keys (send) [-FHKlMRX] ...", so -H
    # is never a standalone token; look for an H inside a flag cluster.
    if not re.search(r"-[A-Za-z]*H", result.stdout):
        raise MissingDependency(
            "this tmux's send-keys lacks the -H flag needed to submit the wake.",
            hint=f"tmux: {version()}. Upgrade to a tmux with 'send-keys -H'.",
        )


def display(target: str, fmt: str) -> str | None:
    """Evaluate a tmux format string against *target*, or None if it is gone."""
    result = run(["tmux", "display-message", "-p", "-t", target, fmt])
    return result.stdout.strip() if result.ok else None


def pane_exists(pane: str) -> bool:
    """Whether *pane* resolves to a live pane."""
    return display(pane, "#{pane_id}") is not None


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
    a wake was accepted — that is confirmed from persisted CLI events.
    """
    result = run(["tmux", "capture-pane", "-p", "-t", pane])
    return result.stdout if result.ok else None


def paste(pane: str, text: str, buffer_name: str) -> None:
    """Paste *text* into *pane* as one block via a named tmux buffer.

    ``-d`` deletes the buffer after pasting so repeated wakes do not accumulate
    buffers in the server.

    Raises:
        SkillError: If the buffer could not be loaded or pasted.
    """
    loaded = run(["tmux", "load-buffer", "-b", buffer_name, "-"], input_text=text)
    if not loaded.ok:
        raise SkillError(f"tmux load-buffer failed: {loaded.stderr}")
    pasted = run(["tmux", "paste-buffer", "-b", buffer_name, "-t", pane, "-d"])
    if not pasted.ok:
        raise SkillError(f"tmux paste-buffer failed: {pasted.stderr}")


def send_hex(pane: str, *codes: str) -> bool:
    """Send raw bytes to *pane* as hex literals via ``send-keys -H``."""
    return run(["tmux", "send-keys", "-t", pane, "-H", *codes]).ok


def send_keys(pane: str, *keys: str) -> bool:
    """Send named keys (e.g. ``Enter``) to *pane*."""
    return run(["tmux", "send-keys", "-t", pane, *keys]).ok


def submit_enter(pane: str, mode: str = "csi") -> bool:
    """Submit the CLI's input box.

    Args:
        pane: Target pane.
        mode: ``"csi"`` for the CSI-u encoding, ``"named"`` for a plain
            ``Enter`` — the only fallback if CSI-u is not being decoded.
    """
    if mode == "named":
        return send_keys(pane, "Enter")
    return send_hex(pane, *CSI_U_ENTER)


def send_focus_in(pane: str) -> bool:
    """Report terminal focus-in to *pane*."""
    return send_hex(pane, *FOCUS_IN)


def send_focus_out(pane: str) -> bool:
    """Report terminal focus-out to *pane*."""
    return send_hex(pane, *FOCUS_OUT)


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
