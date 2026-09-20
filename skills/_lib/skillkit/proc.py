"""Subprocess, dependency and process-ancestry primitives.

Replaces the shell idioms that were the main portability hazard:

============================  ==========================================
shell                         here
============================  ==========================================
``command -v x``              :func:`which`
``setsid`` / ``nohup ... &``  :func:`detach` (``start_new_session=True``)
``kill -0 "$pid"``            :func:`pid_alive`
``ps -o ppid= -p "$pid"``     :func:`parent_pid` (reads /proc when it exists)
``$(cmd)`` with ``set -e``    :func:`run` with ``check=True``
============================  ==========================================

``setsid(1)`` does not exist on macOS, which is why the shell version carried a
``nohup``-in-a-subshell fallback. :func:`detach` needs no fallback:
``start_new_session=True`` calls ``setsid(2)`` directly and is available on
every POSIX platform Python supports.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .errors import MissingDependency

#: Guard against a cycle in reported parentage wedging the ancestry walk.
_MAX_ANCESTRY_DEPTH = 64


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a :func:`run` call.

    Attributes:
        args: The argument vector that was executed.
        returncode: Process exit status.
        stdout: Captured standard output, trailing newline stripped.
        stderr: Captured standard error, trailing newline stripped.
    """

    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when the command exited zero."""
        return self.returncode == 0

    def lines(self) -> list[str]:
        """Standard output split into non-empty lines."""
        return [line for line in self.stdout.splitlines() if line]


@dataclass(frozen=True)
class BinaryCommandResult:
    """Outcome of a :func:`run_bytes` call.

    Attributes:
        args: The argument vector that was executed.
        returncode: Process exit status.
        stdout: Captured standard output as bytes.
        stderr: Captured standard error as bytes.
    """

    args: Sequence[str]
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        """Return whether the command exited successfully."""
        return self.returncode == 0


def which(command: str) -> str | None:
    """Absolute path to *command* on PATH, or None."""
    return shutil.which(command)


def require(*commands: str) -> None:
    """Assert every named command is on PATH.

    Args:
        *commands: External command names.

    Raises:
        MissingDependency: If any are absent. The message names all of them at
            once so a bare checkout reports every gap in a single run rather
            than one per re-invocation.
    """
    missing = [command for command in commands if which(command) is None]
    if missing:
        raise MissingDependency(
            "required command(s) not found on PATH: " + ", ".join(missing)
        )


def run(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    input_text: str | None = None,
    timeout: float | None = None,
    merge_stderr: bool = False,
) -> CommandResult:
    """Run *args* and capture its output.

    Args:
        args: Argument vector. Never a shell string — no quoting or word
            splitting is applied, which removes an entire class of injection
            and whitespace bugs the shell versions had to defend against.
        cwd: Working directory.
        env: Extra environment variables, merged over the current environment.
        check: Raise on a non-zero exit instead of returning it.
        input_text: Text written to the child's stdin.
        timeout: Seconds before the child is killed.
        merge_stderr: Fold stderr into stdout, for tools that interleave.

    Returns:
        CommandResult: Captured outcome, with output whitespace-stripped.

    Raises:
        subprocess.CalledProcessError: If *check* and the command failed.
        subprocess.TimeoutExpired: If *timeout* elapsed.
    """
    full_env = None
    if env is not None:
        full_env = {**os.environ, **env}

    completed = subprocess.run(  # noqa: S603 - argument vector, never a shell string
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        env=full_env,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    result = CommandResult(
        args=list(args),
        returncode=completed.returncode,
        stdout=(completed.stdout or "").rstrip("\n"),
        stderr=(completed.stderr or "").rstrip("\n"),
    )
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            result.returncode, list(args), result.stdout, result.stderr
        )
    return result


def run_bytes(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    input_bytes: bytes | None = None,
    timeout: float | None = None,
    merge_stderr: bool = False,
) -> BinaryCommandResult:
    """Run *args* while preserving stdout and stderr byte-for-byte.

    This is for external tools whose output encoding is not reliably UTF-8.
    Azure CLI on Windows, for example, can emit CP1252 bytes even when invoked
    from WSL; decoding those with replacement would destroy reviewer names and
    PR descriptions before callers can repair the encoding.

    Args:
        args: Argument vector. Shell strings are never accepted.
        cwd: Working directory.
        env: Extra environment variables, merged over the current environment.
        check: Raise on a non-zero exit instead of returning it.
        input_bytes: Bytes written to the child's stdin.
        timeout: Seconds before the child is killed.
        merge_stderr: Fold stderr into stdout.

    Returns:
        BinaryCommandResult: Captured outcome without text decoding.

    Raises:
        subprocess.CalledProcessError: If *check* is true and the command fails.
        subprocess.TimeoutExpired: If *timeout* elapses.

    Side Effects:
        Executes the requested external process.
    """
    full_env = None
    if env is not None:
        full_env = {**os.environ, **env}

    completed = subprocess.run(  # noqa: S603 - argument vector, never a shell string
        list(args),
        cwd=str(cwd) if cwd is not None else None,
        env=full_env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    result = BinaryCommandResult(
        args=list(args),
        returncode=completed.returncode,
        stdout=completed.stdout or b"",
        stderr=completed.stderr or b"",
    )
    if check and not result.ok:
        raise subprocess.CalledProcessError(
            result.returncode, list(args), result.stdout, result.stderr
        )
    return result


def detach(args: Sequence[str], log_file: str | Path, cwd: str | Path | None = None) -> int:
    """Start *args* as a daemon that outlives this process.

    ``start_new_session=True`` performs the ``setsid(2)`` call that detaches the
    child from the controlling terminal and the caller's process group, so a
    terminal hangup cannot reach it. stdin is ``/dev/null`` and both output
    streams are redirected to *log_file*, matching the shell version's contract.

    Args:
        args: Argument vector for the daemon.
        log_file: Path receiving merged stdout/stderr. Parent dirs are created.
        cwd: Working directory for the child.

    Returns:
        int: PID of the detached process.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab", buffering=0) as sink, open(os.devnull, "rb") as null:
        process = subprocess.Popen(  # noqa: S603 - argument vector, never a shell string
            list(args),
            cwd=str(cwd) if cwd is not None else None,
            stdin=null,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return process.pid


def command_of(pid: int) -> str:
    """The full command line of *pid*, or ``""`` if it cannot be determined.

    A PID alone is not an identity: the kernel recycles PIDs, so a value read
    from a stale pidfile may name an unrelated process. Callers that are about
    to signal a PID should first confirm the command line still looks like the
    program they recorded.

    Uses ``ps``, which is present on Linux, macOS and WSL, rather than
    ``/proc``, which macOS does not have.
    """
    if pid <= 0:
        return ""
    result = run(["ps", "-o", "args=", "-p", str(pid)])
    return result.stdout.strip() if result.ok else ""


def pid_alive(pid: int) -> bool:
    """Whether *pid* names a live process.

    The ``kill -0`` idiom: signal 0 performs the permission and existence check
    without delivering anything. ``EPERM`` means the process exists but is
    owned by someone else, which still counts as alive.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def parent_pid(pid: int) -> int | None:
    """PPID of *pid*, or None if it cannot be determined.

    Prefers ``/proc/<pid>/stat`` on Linux because ancestry is re-checked before
    every wake injection and spawning ``ps`` there costs more than the check.
    Falls back to ``ps``, which is specified by POSIX and present on macOS.
    """
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            raw = stat_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # comm is parenthesised and may itself contain spaces or ')', so the
        # fields after it must be located from the LAST ')'.
        close = raw.rfind(")")
        if close != -1:
            fields = raw[close + 2 :].split()
            if len(fields) >= 2 and fields[1].isdigit():
                return int(fields[1])
        return None

    result = run(["ps", "-o", "ppid=", "-p", str(pid)])
    text = result.stdout.strip()
    return int(text) if result.ok and text.isdigit() else None


def is_ancestor(ancestor: int, pid: int) -> bool:
    """Whether *ancestor* appears anywhere in *pid*'s parent chain."""
    current: int | None = pid
    for _ in range(_MAX_ANCESTRY_DEPTH):
        if current is None or current <= 1:
            return False
        if current == ancestor:
            return True
        current = parent_pid(current)
    return False


def pids_related(first: int, second: int) -> bool:
    """Whether the two PIDs are the same process or one descends from the other.

    Used to bind a tmux pane to a Copilot CLI session: the session's lock PID
    and the pane's PID are related when the session runs in that pane.
    """
    return first == second or is_ancestor(first, second) or is_ancestor(second, first)


def terminate(pid: int, *, timeout: float = 5.0, poll: float = 0.1) -> bool:
    """Stop *pid* with SIGTERM, escalating to SIGKILL after *timeout*.

    Returns:
        bool: True if the process is gone when this returns.
    """
    import time

    if not pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not pid_alive(pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(poll)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(poll)
    return not pid_alive(pid)


def iter_missing(commands: Iterable[str]) -> list[str]:
    """Names among *commands* that are not on PATH, for preflight reporting."""
    return [command for command in commands if which(command) is None]


def is_wsl() -> bool:
    """Whether this is a WSL kernel.

    WSL is the supported way to run these skills on a Windows host; a few
    behaviours (clipboard, interop paths) key off it.
    """
    if sys.platform != "linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text(errors="replace").lower()
    except OSError:
        return False
