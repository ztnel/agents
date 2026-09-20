"""skillkit — shared primitives for agent skills.

The skills are glue: they drive unix CLI tools and shuttle JSON between them.
This package holds the primitives that every skill needs so a portability fix
lands once not multiple times.

Target platforms: Linux, macOS and WSL. The rules that keep that true:

* Never shell out for something the standard library does. ``hashlib`` instead
  of ``sha1sum``/``shasum``, ``os.stat`` instead of ``stat -c``/``stat -f``,
  ``re`` instead of ``grep``/``sed``/``awk``, ``datetime`` instead of ``date``.
  Those tools differ between GNU and BSD userland and are the single largest
  source of macOS breakage.
* Never rely on a shell to split, quote or glob. Argument vectors are lists.
* ``tmux`` is an assumed hard dependency (the wake transport needs it); it has
  no native Windows port, so native Windows is out of scope by design. WSL is
  the supported path there.

Import from an entry point with :func:`skillkit.bootstrap.ensure_path`, or rely
on the ``_lib`` directory already being on ``sys.path``.

What this module re-exports, and why that line is where it is: the three
*universal* modules — :mod:`~skillkit.errors`, :mod:`~skillkit.cli` and
:mod:`~skillkit.proc` — are re-exported whole, because every entry point needs
them and they cost nothing beyond interpreter startup. The remaining modules are
imported by path (``from skillkit.tuicrio import ...``) for one of two reasons:
they bind to a specific external tool and so are only meaningful to a script
that already depends on it (:mod:`~skillkit.tuicrio`, :mod:`~skillkit.tmuxio`,
:mod:`~skillkit.gitio`, :mod:`~skillkit.copilot`), or they carry a measurable
import cost (:mod:`~skillkit.paths` pulls in ``hashlib`` + ``json`` for ~7 ms).
Partially re-exporting a module is the one thing to avoid — it makes
``from skillkit import SkillError`` work and ``from skillkit import UsageError``
fail for no discoverable reason.
"""

from .cli import (
    METADATA_RE,
    Metadata,
    detail,
    die,
    emit,
    emit_all,
    error,
    info,
    parse_metadata,
    read_metadata,
    run_main,
    warn,
)
from .errors import MissingDependency, SkillError, UsageError
from .proc import (
    BinaryCommandResult,
    CommandResult,
    command_of,
    detach,
    is_ancestor,
    is_wsl,
    iter_missing,
    parent_pid,
    pid_alive,
    pids_related,
    require,
    run,
    run_bytes,
    terminate,
    which,
)

__all__ = [
    "METADATA_RE",
    "BinaryCommandResult",
    "CommandResult",
    "Metadata",
    "MissingDependency",
    "SkillError",
    "UsageError",
    "command_of",
    "detach",
    "detail",
    "die",
    "emit",
    "emit_all",
    "error",
    "info",
    "is_ancestor",
    "is_wsl",
    "iter_missing",
    "parent_pid",
    "parse_metadata",
    "pid_alive",
    "pids_related",
    "read_metadata",
    "require",
    "run",
    "run_bytes",
    "run_main",
    "terminate",
    "warn",
    "which",
]

__version__ = "1.0.0"
