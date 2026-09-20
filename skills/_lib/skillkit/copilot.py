"""Copilot CLI session state: locate sessions, bind panes, confirm wakes.

A running CLI session owns a directory under the session-state root containing
``inuse.<pid>.lock`` files and an append-only ``events.jsonl``. Two facts make
this the right substrate for the wake transport:

* A session is bound to a tmux pane when one of its lock PIDs shares a process
  ancestry with the pane's PID. Resolving the pane *from* the session is more
  reliable than trusting ``$TMUX_PANE``, which a daemon launched by
  ``tmux new-window`` inherits from the wrong window.
* ``events.jsonl`` is the **acceptance oracle**. A wake counts as delivered only
  once it appears there as a ``user.message`` — terminal rendering is a
  diagnostic and can lie.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import tmuxio
from .errors import SkillError
from .proc import pids_related

#: Prefix and suffix of the per-process lock files in a session directory.
_LOCK_PREFIX = "inuse."
_LOCK_SUFFIX = ".lock"


def session_state_root(override: str | Path | None = None) -> Path:
    """Root holding one directory per CLI session.

    Order: explicit *override*, then ``$COPILOT_SESSION_STATE_DIR``, then
    ``~/.copilot/session-state``.
    """
    if override:
        return Path(override)
    env = os.environ.get("COPILOT_SESSION_STATE_DIR")
    return Path(env) if env else Path.home() / ".copilot" / "session-state"


@dataclass(frozen=True)
class CliSession:
    """A Copilot CLI session directory."""

    session_id: str
    directory: Path

    @property
    def events_file(self) -> Path:
        """Path to the session's append-only event log."""
        return self.directory / "events.jsonl"

    def lock_pids(self) -> list[int]:
        """PIDs holding ``inuse.*.lock`` files, i.e. live users of the session."""
        pids = []
        for lock in self.directory.glob(f"{_LOCK_PREFIX}*{_LOCK_SUFFIX}"):
            raw = lock.name[len(_LOCK_PREFIX) : -len(_LOCK_SUFFIX)]
            if raw.isdigit():
                pids.append(int(raw))
        return pids

    def is_active(self) -> bool:
        """Whether any process currently holds this session."""
        return bool(self.lock_pids())


def iter_sessions(root: str | Path | None = None) -> list[CliSession]:
    """Every session directory under *root*."""
    base = session_state_root(root)
    if not base.is_dir():
        return []
    return [
        CliSession(session_id=child.name, directory=child)
        for child in sorted(base.iterdir())
        if child.is_dir()
    ]


def get_session(session_id: str, root: str | Path | None = None) -> CliSession:
    """The session directory for *session_id*.

    Raises:
        SkillError: If it does not exist (exit 4, matching the shell contract).
    """
    directory = session_state_root(root) / session_id
    if not directory.is_dir():
        raise SkillError(
            f"Copilot session '{session_id}' does not exist under {session_state_root(root)}.",
            code=4,
        )
    return CliSession(session_id=session_id, directory=directory)


def session_for_pane_pid(pane_pid: int, root: str | Path | None = None) -> CliSession:
    """The session running in the pane whose process is *pane_pid*.

    Raises:
        SkillError: If no session matches, or if several do — an ambiguous
            match must never be resolved by guessing, because delivering a wake
            to the wrong pane types text into whatever is running there.
    """
    matches = [
        session
        for session in iter_sessions(root)
        if any(pids_related(pane_pid, lock_pid) for lock_pid in session.lock_pids())
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SkillError(f"no active Copilot session matches pane PID {pane_pid}.", code=4)
    names = ", ".join(session.session_id for session in matches)
    raise SkillError(f"multiple Copilot sessions match pane PID {pane_pid}: {names}", code=4)


def pane_for_session(session: CliSession) -> str:
    """The tmux pane id hosting *session*.

    Raises:
        SkillError: If no live pane hosts it, or if several do.
    """
    lock_pids = session.lock_pids()
    matches: list[str] = []
    for pane in tmuxio.list_panes():
        if any(pids_related(pane.pane_pid, lock_pid) for lock_pid in lock_pids):
            if pane.pane_id not in matches:
                matches.append(pane.pane_id)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SkillError(f"no live tmux pane hosts Copilot session '{session.session_id}'.", code=4)
    raise SkillError(
        f"multiple tmux panes host session '{session.session_id}': {', '.join(matches)}",
        code=4,
    )


def pane_still_bound(pane: str, session: CliSession) -> bool:
    """Whether *pane* still hosts *session*.

    Checked before **every** injection, not only at startup. If the agent exited
    and a shell reclaimed the pane, pasting a prompt and pressing Enter would
    execute it as a shell command — so a stale binding is a safety problem, not
    just a correctness one.
    """
    pid = tmuxio.pane_pid(pane)
    if pid is None:
        return False
    return any(pids_related(pid, lock_pid) for lock_pid in session.lock_pids())


def events_contain(
    events_file: str | Path,
    token: str,
    *,
    from_offset: int = 0,
    event_type: str = "user.message",
) -> bool:
    """Whether *token* appears in a persisted *event_type* event.

    Scans from a byte offset so confirmation stays O(new bytes): the log grows
    to megabytes over a long session and re-parsing it in full on every 0.1 s
    poll dominated the watcher's cost.

    Args:
        events_file: Path to ``events.jsonl``.
        token: Substring that identifies the wake.
        from_offset: Byte offset to resume from. Reset to 0 if the file has
            since shrunk (rotated or truncated).
        event_type: Event ``type`` that counts as acceptance.
    """
    path = Path(events_file)
    try:
        offset = 0 if path.stat().st_size < from_offset else from_offset
        with open(path, encoding="utf-8", errors="replace") as stream:
            stream.seek(offset)
            for line in stream:
                # Cheap substring reject before the expensive JSON parse; most
                # lines in a busy log are irrelevant.
                if token not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != event_type:
                    continue
                if token in (event.get("data") or {}).get("content", ""):
                    return True
    except OSError:
        return False
    return False
