#!/usr/bin/env python3
"""Presentation review-feedback MCP server (stdio).

Run through the plugin's ``mcp.json``: ``uv run --project skills/_lib`` provides
``mcp``. Common tools come from :mod:`skillkit.feedback`; this file adds only
``connect_review`` and the presentation connector over the local ``/agent/*``
routes of a running ``presentation.py open`` server.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

import httpx  # noqa: E402

from skillkit.feedback import (  # noqa: E402
    Inbox, Message, Review, Reviews, create_server, default_inbox, message, serve,
)
from presentation import resolve, session_dir  # noqa: E402


class Presentation:
    frontend = "presentation"
    semantics = "thread-tail: a reply answers the current human tail, not one message"
    closure_detection = True

    def __init__(self, doc: Path):
        self.doc = doc
        self.identity = str(doc)
        self.state = session_dir(doc) / "session.json"

    async def call(self, route: str, body: dict | None = None, timeout: float = 10) -> dict:
        meta = json.loads(self.state.read_text())
        port = meta["port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("presentation state has an invalid local port")
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.request(
                "GET" if body is None else "POST", f"http://127.0.0.1:{port}{route}",
                headers={"X-Token": meta["token"]}, json=body,
            )
            if response.is_error:
                raise RuntimeError(f"presentation rejected request: {response.text}")
            return response.json()

    async def read(self) -> Review:
        store = self.state.with_name("threads.json")
        if store.is_file() and json.loads(store.read_text()).get("closed"):
            return Review(frontend=self.frontend, closed=True, pending=[], context={})
        raw = await self.call("/agent/read")
        if raw.get("error"):
            raise RuntimeError(f"presentation document error: {raw['error']}")
        pending = [
            message(item, thread["thread"], thread["anchor"])
            for thread in raw["pending"] for item in thread["messages"]
        ]
        return Review(frontend=self.frontend, closed=raw["closed"], pending=pending, context=raw)

    async def presence(self, author: str) -> None:
        """Show the agent as waiting in the viewer; native long-poll in short slices."""
        query = urlencode({"timeout": 1, "model": author})
        while True:
            await self.call(f"/agent/wait?{query}", timeout=6)

    async def reply(self, target: Message, content: str, author: str) -> dict:
        return await self.call(
            "/agent/post", {"thread": target.thread, "content": content, "model": author},
        )


def build(reviews: Reviews):
    server = create_server(
        "presentation-feedback",
        "Converse on a running presentation document through its native agent routes.",
        reviews,
    )

    @server.tool()
    async def connect_review(doc: str) -> dict[str, Any]:
        """Connect the running presentation for a Markdown document (presentation.py open first)."""
        path = resolve(doc)
        connector = Presentation(path)
        if not connector.state.is_file():
            raise ValueError("no presentation session; run presentation.py open <doc.md> first")
        return await reviews.attach(connector)

    return server


def main() -> None:
    reviews = Reviews(Inbox(default_inbox("presentation-feedback")))
    asyncio.run(serve(build(reviews), reviews))


if __name__ == "__main__":
    main()
