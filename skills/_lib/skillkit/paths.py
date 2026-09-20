"""Filesystem locations, hashing and crash-safe writes.

Every helper here replaces a shell construct that behaves differently between
GNU and BSD userland:

* ``sha1sum`` (GNU) vs ``shasum`` (macOS)  ->  :func:`short_hash`, via
  ``hashlib``. The shell version shelled out to ``python3`` for exactly this,
  paying ~32 ms per call to avoid the divergence; here it is a function call.
* ``mktemp -d`` flag differences           ->  ``tempfile``.
* Non-atomic ``> file`` truncate-and-write ->  :func:`write_atomic`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

#: Truncation length for :func:`short_hash`. 16 hex chars of SHA-1 is ample for
#: keying local state files and keeps generated filenames readable.
_SHORT_HASH_CHARS = 16


def state_home() -> Path:
    """XDG state root: ``$XDG_STATE_HOME``, else ``~/.local/state``."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def cache_home() -> Path:
    """XDG cache root: ``$XDG_CACHE_HOME``, else ``~/.cache``."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def config_home() -> Path:
    """XDG config root: ``$XDG_CONFIG_HOME``, else ``~/.config``."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def state_dir(*parts: str, create: bool = False) -> Path:
    """Path under the XDG state root, e.g. ``state_dir("tuicr", "watch")``."""
    path = state_home().joinpath(*parts)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir() -> Path:
    """Temporary directory root, honouring ``$TMPDIR`` as macOS requires."""
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir())


def working_file(prefix: str, suffix: str = "", *, directory: str | Path | None = None) -> Path:
    """Reserve an empty private scratch file outside the system temp directory.

    Args:
        prefix: Descriptive filename prefix, without path separators.
        suffix: Optional filename suffix, including its leading dot.
        directory: Parent directory. Defaults to the current working directory.

    Returns:
        An existing empty file reserved exclusively for the caller.

    Raises:
        FileExistsError: If a unique name cannot be reserved after 100 attempts.
        OSError: If the parent directory cannot be created or written.

    Side Effects:
        Creates a mode-0600 hidden file below ``directory``. Callers own its
        cleanup. Unlike :func:`temp_dir`, this never uses ``/tmp``.
    """
    parent = Path(directory) if directory is not None else Path.cwd()
    parent.mkdir(parents=True, exist_ok=True)
    safe_prefix = prefix.replace(os.sep, "-")
    for _ in range(100):
        path = parent / f".{safe_prefix}-{uuid.uuid4().hex}{suffix}"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        return path
    raise FileExistsError(f"could not reserve a scratch file under {parent}")


def working_dir(prefix: str, *, directory: str | Path | None = None) -> Path:
    """Create a private scratch directory outside the system temp directory.

    Args:
        prefix: Descriptive directory-name prefix, without path separators.
        directory: Parent directory. Defaults to the current working directory.

    Returns:
        A newly created empty directory reserved for the caller.

    Raises:
        FileExistsError: If a unique directory cannot be created after 100 attempts.
        OSError: If the parent directory cannot be created or written.

    Side Effects:
        Creates a mode-0700 hidden directory below ``directory``. Callers own
        its cleanup. Unlike :func:`temp_dir`, this never uses ``/tmp``.
    """
    parent = Path(directory) if directory is not None else Path.cwd()
    parent.mkdir(parents=True, exist_ok=True)
    safe_prefix = prefix.replace(os.sep, "-")
    for _ in range(100):
        path = parent / f".{safe_prefix}-{uuid.uuid4().hex}"
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"could not reserve a scratch directory under {parent}")


def short_hash(*parts: str, length: int = _SHORT_HASH_CHARS) -> str:
    """Stable short hex digest of *parts*.

    Args:
        *parts: Components joined with ``|`` before hashing, so callers get the
            same key for the same tuple without inventing a separator.
        length: Hex characters to keep.

    Returns:
        str: Lowercase hex digest prefix.
    """
    joined = "|".join(parts).encode("utf-8")
    return hashlib.sha1(joined, usedforsecurity=False).hexdigest()[:length]


def write_atomic(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically.

    Writes a sibling temp file, flushes it to disk, then ``os.replace``s it into
    place. A reader therefore sees either the old file or the new one, never a
    half-written one — which matters for the watcher ledger, read by one process
    while another rewrites it.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding=encoding) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_json_atomic(path: str | Path, payload: Any, *, indent: int | None = None) -> None:
    """Serialise *payload* as JSON and write it via :func:`write_atomic`."""
    write_atomic(path, json.dumps(payload, indent=indent, sort_keys=indent is not None) + "\n")


def read_json(path: str | Path, default: Any = None) -> Any:
    """Load JSON from *path*, returning *default* if absent or unparseable.

    State files are advisory: a corrupted ledger must degrade to "no history"
    rather than crash a long-running daemon.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError):
        return default


def file_size(path: str | Path) -> int:
    """Size of *path* in bytes, or 0 if it cannot be stat-ed."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
