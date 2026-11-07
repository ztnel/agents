"""tmux control: panes, windows, buffers and synthetic key delivery.

Two consumers. The ``tmux`` skill uses the pane-management primitives to open,
inspect and close explicitly targeted panes; application skills must not drive
terminals themselves. The tuicr watcher uses the buffer and key primitives as
the transport that carries a wake into a running Copilot CLI pane. tmux has no
native Windows port, which is what scopes these skills to Linux, macOS and WSL.

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
from .proc import require, run, which

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


# --- Pane management ---------------------------------------------------------

#: Exact pane IDs (``%12``). Mutating helpers accept nothing looser, so a stale
#: window name or index can never resolve to an unrelated pane.
PANE_ID_RE = re.compile(r"^%[0-9]+$")

_PANE_FIELDS = (
    ("pane_id", "#{pane_id}"),
    ("window_id", "#{window_id}"),
    ("session_id", "#{session_id}"),
    ("session_name", "#{session_name}"),
    ("window_index", "#{window_index}"),
    ("window_name", "#{window_name}"),
    ("pane_index", "#{pane_index}"),
    ("pane_pid", "#{pane_pid}"),
    ("pane_active", "#{pane_active}"),
    ("window_active", "#{window_active}"),
    ("pane_width", "#{pane_width}"),
    ("pane_height", "#{pane_height}"),
    ("pane_current_command", "#{pane_current_command}"),
    ("pane_current_path", "#{pane_current_path}"),
)
_PANE_FORMAT = "\t".join(fmt for _, fmt in _PANE_FIELDS)
_INT_FIELDS = {"window_index", "pane_index", "pane_pid", "pane_width", "pane_height"}
_BOOL_FIELDS = {"pane_active", "window_active"}


def require_pane_id(pane: str) -> str:
    """Return *pane* if it is an exact pane ID, else raise (exit 2)."""
    if not PANE_ID_RE.match(pane or ""):
        raise SkillError(f"expected an exact pane id like %12, got {pane!r}", code=2)
    return pane


def _parse_pane(line: str) -> dict | None:
    values = line.split("\t")
    if len(values) != len(_PANE_FIELDS):
        return None
    pane: dict = {}
    for (key, _), value in zip(_PANE_FIELDS, values):
        if key in _INT_FIELDS:
            pane[key] = int(value) if value.isdigit() else None
        elif key in _BOOL_FIELDS:
            pane[key] = value == "1"
        else:
            pane[key] = value
    return pane


def describe_panes(target: str | None = None, *, scope: str = "all") -> list[dict]:
    """Structured description of panes.

    Args:
        target: Session, window or pane the *scope* is relative to.
        scope: ``all`` for every pane on the server, ``session`` for the
            target's session, ``window`` for the target's window.

    Raises:
        SkillError: If tmux cannot list panes (exit 4: no server or bad target).
    """
    args = ["tmux", "list-panes", "-F", _PANE_FORMAT]
    if scope == "all":
        args.insert(2, "-a")
    else:
        if scope == "session":
            args.insert(2, "-s")
        if target:
            args[2:2] = ["-t", target]
    result = run(args)
    if not result.ok:
        raise SkillError(f"tmux list-panes failed: {result.stderr or 'no tmux server'}", code=4)
    return [pane for pane in map(_parse_pane, result.lines()) if pane]


def describe_pane(pane: str) -> dict | None:
    """Description of one exact pane, or None if it no longer exists."""
    result = run(["tmux", "display-message", "-p", "-t", require_pane_id(pane), _PANE_FORMAT])
    if not result.ok:
        return None
    described = _parse_pane(result.stdout)
    return described if described and described["pane_id"] == pane else None


def open_pane(
    command: str,
    *,
    cwd: str,
    target: str | None = None,
    window_name: str | None = None,
    split: str | None = None,
    size: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Start *command* in a new detached pane and return its pane ID.

    A new window is created unless *split* (``h`` or ``v``) is given, in which
    case *target* (an exact pane ID) is split. Nothing receives focus, and no
    session, server or global option is changed. Environment values are passed
    as literal ``-e`` arguments, never interpolated into *command*.

    Raises:
        SkillError: If tmux refuses (exit 4).
    """
    if split:
        if split not in ("h", "v"):
            raise SkillError(f"split must be 'h' or 'v', got {split!r}", code=2)
        args = ["tmux", "split-window", "-d", f"-{split}", "-t", require_pane_id(target or "")]
        if size:
            args += ["-l", size]
    else:
        args = ["tmux", "new-window", "-d"]
        if target:
            # A trailing colon targets the session, so tmux picks a free index.
            args += ["-t", f"{target}:" if not target.endswith(":") else target]
        if window_name:
            args += ["-n", window_name]
    args += ["-P", "-F", "#{pane_id}", "-c", cwd]
    for name, value in (env or {}).items():
        args += ["-e", f"{name}={value}"]
    created = run([*args, command])
    pane = created.stdout.strip()
    if not created.ok or not PANE_ID_RE.match(pane):
        raise SkillError(f"tmux could not open a pane: {created.stderr or pane}", code=4)
    return pane


def focus_pane(pane: str) -> None:
    """Make *pane* the active pane of its window and its window current."""
    require_pane_id(pane)
    for args in (["tmux", "select-window", "-t", pane], ["tmux", "select-pane", "-t", pane]):
        result = run(args)
        if not result.ok:
            raise SkillError(f"tmux could not focus {pane}: {result.stderr}", code=4)


def resize_pane(pane: str, *, width: str | None = None, height: str | None = None) -> None:
    """Resize *pane* to *width* columns and/or *height* rows (``N`` or ``N%``)."""
    args = ["tmux", "resize-pane", "-t", require_pane_id(pane)]
    if width:
        args += ["-x", width]
    if height:
        args += ["-y", height]
    if not (width or height):
        raise SkillError("resize needs a width or height", code=2)
    result = run(args)
    if not result.ok:
        raise SkillError(f"tmux could not resize {pane}: {result.stderr}", code=4)


def close_pane(pane: str) -> None:
    """Kill exactly *pane*; its window closes only if it was the last pane."""
    result = run(["tmux", "kill-pane", "-t", require_pane_id(pane)])
    if not result.ok:
        raise SkillError(f"tmux could not close {pane}: {result.stderr}", code=4)


def capture_lines(pane: str, lines: int | None = None) -> str:
    """Visible text of *pane*, optionally including *lines* of scrollback."""
    args = ["tmux", "capture-pane", "-p", "-t", require_pane_id(pane)]
    if lines:
        args += ["-S", f"-{lines}"]
    result = run(args)
    if not result.ok:
        raise SkillError(f"tmux could not capture {pane}: {result.stderr}", code=4)
    return result.stdout
