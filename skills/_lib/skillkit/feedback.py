"""Shared review-feedback contract for skill-owned MCP servers.

Each review skill (tuicr, presentation, ...) ships its own MCP server with one
frontend-specific ``connect_review`` tool and a :class:`Connector`. This module
supplies everything else so every frontend behaves identically:

* the wire models (:class:`Message`, :class:`Review`, :class:`Feedback`),
* a durable SQLite :class:`Inbox` that turns native pending-state snapshots into
  ordered events with explicit acknowledgement,
* :class:`Reviews`, which monitors connected reviews, and
* :func:`create_server`, which registers the common tools.

No wake injection, work queue, or approval lives here. Closure never implies
approval; frontends report their own close detail. Requires ``mcp`` (see
``_lib/pyproject.toml``); the rest of skillkit stays stdlib-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

BATCH_LIMIT = 50
POLL_SECONDS = 1.0


class Message(BaseModel):
    id: str
    thread: str
    content: str
    anchor: Any
    version: str
    comment_type: str | None = None


class Review(BaseModel):
    frontend: str
    closed: bool | None
    pending: list[Message]
    closure: dict[str, Any] | None = None
    context: dict[str, Any]


class FeedbackEvent(BaseModel):
    sequence: int
    kind: Literal["message", "withdrawn", "closure"]
    message: Message | None = None
    closed: bool | None = None
    closure: dict[str, Any] | None = None
    superseded: bool = False


class Batch(BaseModel):
    id: str
    events: list[FeedbackEvent]


class Feedback(BaseModel):
    status: Literal["feedback", "timeout", "closed"]
    closed: bool | None
    batch: Batch | None = None


class Connector(Protocol):
    frontend: str
    semantics: str
    closure_detection: bool
    identity: str

    async def read(self) -> Review: ...

    async def reply(self, target: Message, content: str, author: str) -> dict: ...


def version(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def message(item: dict, thread: str, anchor: Any, comment_type: str | None = None) -> Message:
    return Message(
        id=item["id"], thread=thread, content=item["content"], anchor=anchor,
        version=version(item["content"]), comment_type=comment_type,
    )


def default_inbox(name: str) -> Path:
    configured = os.environ.get("FEEDBACK_INBOX")
    if configured:
        return Path(configured)
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return state / name / "inbox.sqlite3"


class Inbox:
    """Durable, single-consumer feedback batches; delivery never acknowledges work."""

    def __init__(self, path: Path | None = None):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:")
        self.persistent = path is not None
        if path is not None:
            path.chmod(0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (
                identity TEXT PRIMARY KEY, snapshot TEXT NOT NULL, closed TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                identity TEXT NOT NULL, payload TEXT NOT NULL,
                batch TEXT, acknowledged INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS outstanding_batch
                ON batches(identity) WHERE acknowledged=0;
            CREATE INDEX IF NOT EXISTS message_revision
                ON events(identity, json_extract(payload, '$.message.id'), sequence);
        """)

    def observe(self, identity: str, review: Review) -> bool:
        current = {item.id: item.model_dump(mode="json") for item in review.pending}
        row = self.db.execute(
            "SELECT snapshot, closed FROM reviews WHERE identity=?", (identity,),
        ).fetchone()
        previous = json.loads(row[0]) if row else {}
        if row and previous == current and json.loads(row[1]) == review.closed:
            return False
        events: list[dict[str, Any]] = []
        for key, value in current.items():
            if previous.get(key) != value:
                events.append({"kind": "message", "message": value})
        # Closure can hide native messages; that is not evidence of deletion.
        if review.closed is not True:
            for key, value in previous.items():
                if key not in current:
                    events.append({"kind": "withdrawn", "message": value})
        if (row and json.loads(row[1]) != review.closed) or (not row and review.closed is True):
            events.append({"kind": "closure", "closed": review.closed, "closure": review.closure})
        with self.db:
            for payload in events:
                self.db.execute(
                    "INSERT INTO events(identity,payload) VALUES (?,?)",
                    (identity, json.dumps(payload)),
                )
            self.db.execute(
                "INSERT OR REPLACE INTO reviews VALUES (?,?,?)",
                (identity, json.dumps(current), json.dumps(review.closed)),
            )
        return bool(events)

    def receive(self, identity: str) -> Feedback:
        state = self.db.execute(
            "SELECT snapshot,closed FROM reviews WHERE identity=?", (identity,),
        ).fetchone()
        if state is None:
            raise ValueError("review has not been observed")
        current, closed = json.loads(state[0]), json.loads(state[1])
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            batch = self.db.execute(
                "SELECT id FROM batches WHERE identity=? AND acknowledged=0", (identity,),
            ).fetchone()
            if batch is None:
                queued = self.db.execute(
                    "SELECT sequence FROM events WHERE identity=? AND batch IS NULL "
                    "ORDER BY sequence LIMIT ?", (identity, BATCH_LIMIT),
                ).fetchall()
                if queued:
                    batch = (uuid.uuid4().hex,)
                    self.db.execute("INSERT INTO batches(id,identity) VALUES (?,?)", (batch[0], identity))
                    self.db.executemany(
                        "UPDATE events SET batch=? WHERE sequence=?",
                        [(batch[0], row[0]) for row in queued],
                    )
            events = []
            if batch:
                for sequence, payload in self.db.execute(
                    "SELECT sequence,payload FROM events WHERE batch=? ORDER BY sequence", (batch[0],),
                ):
                    event = FeedbackEvent(sequence=sequence, **json.loads(payload))
                    if event.kind == "message" and event.message:
                        latest = self.db.execute(
                            "SELECT MAX(sequence) FROM events WHERE identity=? "
                            "AND json_extract(payload, '$.message.id')=?",
                            (identity, event.message.id),
                        ).fetchone()[0]
                        event.superseded = (
                            current.get(event.message.id) != event.message.model_dump(mode="json")
                            or closed is True
                            or sequence != latest
                        )
                    events.append(event)
        return Feedback(
            status="feedback" if batch else "closed" if closed is True else "timeout",
            closed=closed, batch=Batch(id=batch[0], events=events) if batch else None,
        )

    def acknowledge(self, identity: str, batch_id: str) -> None:
        row = self.db.execute("SELECT identity FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None or row[0] != identity:
            raise ValueError("batch does not belong to this review")
        with self.db:
            self.db.execute("UPDATE batches SET acknowledged=1 WHERE id=?", (batch_id,))
            self.db.execute("UPDATE events SET acknowledged=1 WHERE batch=?", (batch_id,))

    def close(self) -> None:
        self.db.close()


class Reviews:
    """Connected reviews keyed by stable native identity, each with one monitor."""

    def __init__(self, inbox: Inbox):
        self.inbox = inbox
        self.connectors: dict[str, Connector] = {}
        self.monitors: dict[str, asyncio.Task] = {}
        self.changed: dict[str, asyncio.Event] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        for task in self.monitors.values():
            task.cancel()
        results = await asyncio.gather(*self.monitors.values(), return_exceptions=True)
        self.inbox.close()
        for result in results:
            if isinstance(result, Exception):
                raise result

    async def attach(self, connector: Connector) -> dict:
        """Register *connector*; reconnecting the same native review reuses its id."""
        review_id = version(json.dumps([connector.frontend, connector.identity]))
        self.connectors[review_id] = connector
        self.locks.setdefault(review_id, asyncio.Lock())
        await self.observe(review_id)
        previous = self.monitors.get(review_id)
        if previous is None or previous.done():
            self.changed[review_id] = asyncio.Event()
            self.monitors[review_id] = asyncio.create_task(self.monitor(review_id))
        return {
            "review_id": review_id, "frontend": connector.frontend,
            "capabilities": {
                "reply_semantics": connector.semantics,
                "closure_detection": connector.closure_detection,
                "durable_inbox": self.inbox.persistent,
                "consumer": "one outstanding batch per native review",
            },
        }

    def require(self, review_id: str) -> Connector:
        if review_id not in self.connectors:
            raise ValueError("connect this review in the current MCP connection first")
        task = self.monitors[review_id]
        if task.done():
            if not task.cancelled():
                task.result()
            raise RuntimeError("feedback monitor stopped; reconnect the review")
        return self.connectors[review_id]

    async def observe(self, review_id: str) -> bool:
        async with self.locks[review_id]:
            review = await self.connectors[review_id].read()
            return self.inbox.observe(review_id, review)

    async def monitor(self, review_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(POLL_SECONDS)
                if await self.observe(review_id):
                    self.changed[review_id].set()
        finally:
            self.changed[review_id].set()

    async def receive(self, review_id: str) -> Feedback:
        self.require(review_id)
        await self.observe(review_id)
        return self.inbox.receive(review_id)

    async def wait(self, review_id: str, timeout: int, author: str) -> Feedback:
        if not 1 <= timeout <= 300 or not author.strip():
            raise ValueError("wait requires 1-300 seconds and an explicit author")
        connector = self.require(review_id)
        presence = getattr(connector, "presence", None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        shown = asyncio.create_task(presence(author)) if presence else None
        try:
            while True:
                self.changed[review_id].clear()
                feedback = await self.receive(review_id)
                remaining = deadline - loop.time()
                if feedback.status != "timeout" or remaining <= 0:
                    return feedback
                try:
                    await asyncio.wait_for(self.changed[review_id].wait(), remaining)
                except TimeoutError:
                    return await self.receive(review_id)
        finally:
            if shown:
                shown.cancel()
                await asyncio.gather(shown, return_exceptions=True)

    async def reply(
        self, review_id: str, message_id: str, message_version: str, content: str, author: str,
    ) -> dict:
        if not content.strip() or len(content) > 1500 or not author.strip():
            raise ValueError("reply requires 1-1500 characters and an explicit author")
        connector = self.require(review_id)
        review = await connector.read()
        if review.closed:
            raise ValueError("review is closed")
        target = next((item for item in review.pending if item.id == message_id), None)
        if target is None:
            raise ValueError("message is not pending in this review; reread before replying")
        if target.version != message_version:
            raise ValueError("message changed; reread before replying")
        return await connector.reply(target, content, author)


def create_server(name: str, instructions: str, reviews: Reviews) -> FastMCP:
    """Build a server with the common feedback tools; add ``connect_review`` yourself."""
    server = FastMCP(name, instructions=(
        f"{instructions} Receive durable batches and acknowledge them explicitly. "
        "Reads and replies never acknowledge. Closure never implies approval. "
        "No wake injection or work queue."
    ))

    @server.tool()
    async def read_review(review_id: str) -> Review:
        """Read current native context and pending messages; reading is not acknowledgement."""
        return await reviews.require(review_id).read()

    @server.tool()
    async def receive_feedback(review_id: str) -> Feedback:
        """Receive up to 50 ordered events, or replay the outstanding batch.

        Skip superseded message revisions. Withdrawn means no longer pending,
        not necessarily deleted. comment_type is a native hint, not intent.
        """
        return await reviews.receive(review_id)

    @server.tool()
    async def acknowledge_feedback(review_id: str, batch_id: str) -> dict[str, bool]:
        """Acknowledge a completed batch; idempotent and never consumes later events."""
        reviews.require(review_id)
        reviews.inbox.acknowledge(review_id, batch_id)
        return {"acknowledged": True}

    @server.tool()
    async def reply(
        review_id: str, message_id: str, message_version: str, content: str, author: str,
    ) -> dict[str, Any]:
        """Reply at the original native anchor; no work is queued or executed."""
        return await reviews.reply(review_id, message_id, message_version, content, author)

    @server.tool()
    async def wait_for_feedback(
        review_id: str, author: str,
        timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 120,
    ) -> Feedback:
        """Return the outstanding or next batch, waiting up to timeout_seconds.

        Occupies a tool call; it does not wake an idle agent. Cancellation never
        consumes feedback.
        """
        return await reviews.wait(review_id, timeout_seconds, author)

    return server


async def serve(server: FastMCP, reviews: Reviews) -> None:
    try:
        await server.run_stdio_async()
    finally:
        await reviews.close()
