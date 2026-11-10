"""Accepted review work, queued until the human closes the review.

The feedback bridge records an ``accepted`` reply here; the review launcher
reads the open queue when the review closes, so approval cannot hide pending
work, and archives it when the next round opens. Both share the bridge's
inbox database. Stdlib only: launchers run without ``mcp``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS work (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL, message_id TEXT NOT NULL, task TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS open_work ON work(scope) WHERE archived=0;
"""


def default_inbox(name: str) -> Path:
    """Inbox database of the feedback server *name*; ``FEEDBACK_INBOX`` overrides."""
    configured = os.environ.get("FEEDBACK_INBOX")
    if configured:
        return Path(configured)
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return state / name / "inbox.sqlite3"


def scope(*parts: str) -> str:
    """Stable queue key from a frontend's native review coordinates."""
    return json.dumps(parts)


def enqueue(db: sqlite3.Connection, key: str, message_id: str, task: str) -> None:
    with db:
        db.execute("INSERT INTO work(scope,message_id,task) VALUES (?,?,?)", (key, message_id, task))


def pending(path: Path, key: str) -> list[dict]:
    """Open work for *key*; an absent database means nothing is queued."""
    if not path.is_file():
        return []
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        rows = db.execute(
            "SELECT message_id, task FROM work WHERE scope=? AND archived=0 ORDER BY id", (key,),
        ).fetchall()
    finally:
        db.close()
    return [{"message_id": message_id, "task": task} for message_id, task in rows]


def archive(path: Path, key: str) -> None:
    """Close the queue for *key*: a new review round starts empty."""
    if not path.is_file():
        return
    db = sqlite3.connect(path)
    try:
        db.executescript(SCHEMA)
        with db:
            db.execute("UPDATE work SET archived=1 WHERE scope=? AND archived=0", (key,))
    finally:
        db.close()
