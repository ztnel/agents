"""Single-instance pidfile locking.

A watcher must be a singleton per watched session: two daemons on one session
would double-wake the agent. The shell version wrote ``$$`` to a file and
checked it with ``kill -0``; this keeps that on-disk format — a bare PID — so a
Python daemon and any remaining shell tooling can read each other's locks.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import SkillError
from .proc import pid_alive, terminate


class PidFile:
    """A pidfile guarding one long-lived process.

    Stale locks (owner no longer alive) are reclaimed automatically; a live
    owner is reported rather than displaced.

    Use as a context manager so the lock is released on any exit path::

        with PidFile(path) as lock:
            ...
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._held = False

    def owner(self) -> int | None:
        """PID recorded in the file, or None if absent or unreadable."""
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return int(raw) if raw.isdigit() else None

    def live_owner(self) -> int | None:
        """PID of the current owner if it is still running."""
        pid = self.owner()
        return pid if pid is not None and pid_alive(pid) else None

    def acquire(self) -> "PidFile":
        """Claim the lock.

        Raises:
            SkillError: If another live process holds it (exit 8, matching the
                shell contract).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.live_owner()
        if existing is not None:
            raise SkillError(
                f"another process (PID {existing}) already holds {self.path.name}.",
                code=8,
                hint="Stop it first, or use the watch-up helper which reuses a live daemon.",
            )
        self.path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        self._held = True
        return self

    def release(self) -> None:
        """Release the lock if this process owns it."""
        if not self._held:
            return
        if self.owner() == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
        self._held = False

    def stop_owner(self, *, timeout: float = 5.0) -> bool:
        """Terminate the current owner and clear the lock.

        Returns:
            bool: True if no live owner remains.
        """
        pid = self.live_owner()
        stopped = terminate(pid, timeout=timeout) if pid is not None else True
        if stopped:
            try:
                self.path.unlink()
            except OSError:
                pass
        return stopped

    def __enter__(self) -> "PidFile":
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()
