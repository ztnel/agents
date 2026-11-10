#!/usr/bin/env python3
"""tuicr review-feedback MCP server (stdio).

Run through the plugin's ``mcp.json``: ``uv run --project skills/_lib`` provides
``mcp``. Common tools come from :mod:`skillkit.feedback`; this file adds only
``connect_review`` and the tuicr connector over the ``tuicr review`` CLI.

Closure is the close report ``tuicr_up.py`` writes when its tuicr window exits;
a session opened any other way reports ``closed: null`` (unknown).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.feedback import (  # noqa: E402
    Inbox, Message, Review, Reviews, create_server, default_inbox, message, serve,
)
from skillkit.tuicrio import DEFAULT_IGNORE_TYPES  # noqa: E402
from skillkit import workqueue  # noqa: E402
import questions  # noqa: E402
from tuicr_up import anchor, anchor_key, report_path  # noqa: E402

IGNORED = frozenset(DEFAULT_IGNORE_TYPES)


async def output(argv: list[str]) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 20)
    except (asyncio.CancelledError, TimeoutError):
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError(f"{argv[0]} failed: {stderr.decode().strip()}")
    return stdout


async def command(argv: list[str]) -> object:
    return json.loads(await output(argv))


class Tuicr:
    frontend = "tuicr"
    semantics = "positional: later replies at the same anchor answer earlier comments"
    closure_detection = True
    policy = (
        "Work waits for closure: reply accepted with a task to queue each change, "
        "mirror the queue in your todo list, and edit nothing until the closure "
        "event returns the queue. Then apply it and open a new review round."
    )

    def __init__(self, repo: str, slug: str, session: Path):
        self.repo, self.slug, self.session = repo, slug, session
        self.identity = str(session)
        self.queue_scope = workqueue.scope(repo, slug)

    async def fingerprint(self) -> str:
        """HEAD, status and dirty-file content: any edit to the tree changes it."""
        git = ["git", "-C", self.repo]
        digest = hashlib.sha256(await output([*git, "rev-parse", "HEAD"]))
        status = await output([*git, "status", "--porcelain", "-z", "--untracked-files=all"])
        digest.update(status)
        for entry in status.split(b"\0"):
            path = Path(self.repo, entry[3:].decode(errors="surrogateescape"))
            if len(entry) > 3 and path.is_file():
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    async def read(self) -> Review:
        try:
            raw = await command([
                "tuicr", "review", "comments", "--repo", self.repo, "--session", str(self.session),
            ])
        except RuntimeError:
            # Closing wipes settled comments and tuicr drops a session left empty,
            # possibly before the close report lands; the report decides closure.
            if self.session.exists():
                raise
            raw = []
        if not isinstance(raw, list):
            raise ValueError("tuicr comments must return a JSON array")
        registered = questions.registered_ids(self.repo, self.slug)
        replies: dict[str, list[str]] = {}
        for item in raw:
            if item.get("comment_type") == "reply":
                replies.setdefault(anchor_key(item), []).append(item["created_at"])
        pending = [
            message(item, anchor_key(item), anchor(item), item.get("comment_type"))
            for item in raw
            if item.get("comment_type") not in IGNORED and item["id"] not in registered
            and not any(stamp > item["created_at"] for stamp in replies.get(anchor_key(item), []))
        ]
        report = report_path(self.repo, self.slug)
        closure = None
        # An active session is a new round; its predecessor's report is stale.
        if report.is_file() and not await active(self.repo, self.slug):
            verdict = json.loads(report.read_text())
            closure = {
                "verdict": verdict.get("verdict"), "report": str(report),
                "queued": verdict.get("queued", []),
            }
        return Review(
            frontend=self.frontend, closed=True if closure else None, pending=pending,
            closure=closure, context={"slug": self.slug, "comments": raw},
        )

    async def reply(self, target: Message, content: str, author: str) -> dict:
        place = target.anchor
        argv = [
            "tuicr", "review", "add", "--repo", self.repo, "--session", str(self.session),
            "--type", "reply", "--username", author,
        ]
        if place["path"]:
            argv.extend(["--target-file", place["path"]])
            if place["start_line"]:
                argv.extend([
                    "--line", str(place["start_line"]), "--end-line", str(place["end_line"]),
                    "--side", place["side"],
                ])
        delivered = await command([*argv, "--", content])
        current = await self.read()
        if not any(item["id"] == delivered["id"] for item in current.context["comments"]):
            raise RuntimeError("tuicr returned a reply but it is absent from this session")
        return delivered


async def active(repo: str, slug: str) -> bool:
    sessions = await command(["tuicr", "review", "list", "--repo", repo])
    return any(item["slug"] == slug and item.get("active") for item in sessions)


async def locate(repo: str, slug: str | None) -> tuple[str, Path]:
    """Resolve *slug* (default: the active session) to tuicr's canonical session path."""
    sessions = await command(["tuicr", "review", "list", "--repo", repo])
    matches = [
        item for item in sessions
        if (item["slug"] == slug if slug else item.get("active"))
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {'session ' + slug if slug else 'active session'} in {repo}; "
            f"found {len(matches)}"
        )
    return matches[0]["slug"], Path(matches[0]["path"])


def build(reviews: Reviews):
    server = create_server(
        "tuicr-feedback",
        "Converse on a tuicr code review through the tuicr review CLI. "
        "Code approval is the close report's verdict, never closure alone.",
        reviews,
    )

    @server.tool()
    async def connect_review(repo: str, session: str | None = None) -> dict[str, Any]:
        """Connect a tuicr review by repo and slug; default is the repo's active session."""
        root = str(Path(repo).expanduser().resolve(strict=True))
        slug, path = await locate(root, session)
        return await reviews.attach(Tuicr(root, slug, path))

    return server


def main() -> None:
    reviews = Reviews(Inbox(default_inbox("tuicr-feedback")))
    asyncio.run(serve(build(reviews), reviews))


if __name__ == "__main__":
    main()
