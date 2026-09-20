"""Output conventions and entry-point plumbing shared by every skill script.

The skills speak a small, stable protocol to their callers — the agent reading
their output, and the shell test suites asserting on it:

* ``==> message``    progress, on stdout.
* ``WARN: message``  non-fatal problem, on stderr.
* ``ERROR: message`` fatal, on stderr, paired with a non-zero exit.
* ``KEY=VALUE``      machine-readable metadata, on stdout, shell-quoted so the
  caller can ``eval`` the block (``branch_parse.py`` and ``worktree_up.py``
  are consumed exactly that way).

Preserving these byte-for-byte is what lets the port swap the implementation
language without touching a single call site in ``SKILL.md`` or the tests.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from typing import Callable, Mapping, NoReturn, Sequence

from .errors import SkillError

#: Matches one ``KEY=VALUE`` metadata line, ignoring surrounding whitespace.
METADATA_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

Metadata = dict[str, str]


def info(message: str) -> None:
    """Print a progress line as ``==> message`` on stdout."""
    print(f"==> {message}", flush=True)


def detail(message: str) -> None:
    """Print an indented continuation line under an :func:`info` heading."""
    print(f"    {message}", flush=True)


def warn(message: str) -> None:
    """Print ``WARN: message`` on stderr."""
    print(f"WARN: {message}", file=sys.stderr, flush=True)


def error(message: str) -> None:
    """Print ``ERROR: message`` on stderr without exiting."""
    print(f"ERROR: {message}", file=sys.stderr, flush=True)


def die(message: str, code: int = 1, hint: str | None = None) -> NoReturn:
    """Report a fatal error and raise :class:`SkillError` carrying *code*."""
    raise SkillError(message, code=code, hint=hint)


def emit(key: str, value: object) -> None:
    """Print one shell-``eval``-safe ``KEY=VALUE`` metadata line.

    The value is quoted with :func:`shlex.quote`, so paths containing spaces
    survive ``eval "$(script.sh ...)"`` intact — a case the hand-rolled shell
    emitters got wrong whenever a worktree path had a space in it.
    """
    print(f"{key}={shlex.quote(str(value))}", flush=True)


def emit_all(items: Mapping[str, object]) -> None:
    """Print a whole metadata block in iteration order."""
    for key, value in items.items():
        emit(key, value)


def parse_metadata(text: str) -> Metadata:
    """Parse a ``KEY=VALUE`` block back into a dict.

    Shell quoting applied by :func:`emit` is undone, so a round trip through
    ``emit`` and ``parse_metadata`` is lossless. Lines that are not metadata
    (progress output, blank lines) are ignored, letting a caller parse the
    output of a script that also prints ``==>`` lines.
    """
    found: Metadata = {}
    for line in text.splitlines():
        match = METADATA_RE.match(line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2)
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = [raw]
        found[key] = parts[0] if parts else ""
    return found


def read_metadata(path: str | Path) -> Metadata:
    """Parse a ``KEY=VALUE`` block from a file."""
    return parse_metadata(Path(path).read_text(encoding="utf-8", errors="replace"))


def run_main(entry: Callable[[Sequence[str]], int | None], argv: Sequence[str] | None = None) -> NoReturn:
    """Invoke *entry* as a script's ``main``, mapping errors onto exit codes.

    Converts :class:`SkillError` into its diagnosed message plus carried exit
    code, turns ``Ctrl-C`` into the conventional 130, and lets a broken pipe
    (``script.py | head``) exit quietly instead of dumping a traceback.

    Args:
        entry: Callable taking argv-without-program-name, returning an exit code.
        argv: Override for ``sys.argv[1:]``, for testing.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        raise SystemExit(entry(args) or 0)
    except SkillError as exc:
        if getattr(exc, "raw", False):
            print(str(exc), file=sys.stderr, flush=True)
        else:
            error(str(exc))
        if exc.hint:
            print(f"       {exc.hint}", file=sys.stderr, flush=True)
        raise SystemExit(exc.code) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except BrokenPipeError:
        raise SystemExit(0) from None
