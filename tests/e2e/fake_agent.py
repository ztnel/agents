#!/usr/bin/env python3
"""Deterministic stand-in for the developer agent, speaking the real feedback MCP protocol.

Follows the developer contract with scripted content: agree a brief on the
presentation page, apply each solution round, review it in tuicr, apply queued
work, and on approval stage through the ledger, commit and push. It exercises
the servers, launchers, ledger and commit tooling without a model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
MODEL = "e2e-fake-agent"
SERVERS = {"presentation": SKILLS / "presentation/lib/feedback_server.py",
           "tuicr": SKILLS / "tuicr/lib/feedback_server.py"}


def run(*argv: str | Path, cwd: Path) -> str:
    result = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True)
    print(f"$ {' '.join(map(str, argv))[:200]}\n{result.stdout}{result.stderr}", flush=True)
    result.check_returncode()
    return result.stdout


@asynccontextmanager
async def server(name: str):
    params = StdioServerParameters(command=sys.executable, args=[str(SERVERS[name])], env=dict(os.environ))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def call(session: ClientSession, tool: str, **arguments) -> dict:
    result = await session.call_tool(tool, arguments)
    if result.isError:
        raise RuntimeError(f"{tool}: {result.content}")
    return result.structuredContent or json.loads(result.content[0].text)


async def review(session: ClientSession, review_id: str, verdict: str) -> dict | None:
    """Answer every message with ``verdict`` until closure; return the closure."""
    while True:
        feedback = await call(session, "wait_for_feedback", review_id=review_id, author=MODEL, timeout_seconds=30)
        batch = feedback.get("batch")
        if not batch:
            continue
        closure = None
        for event in batch["events"]:
            if event["superseded"]:
                continue
            if event["kind"] == "message":
                message = event["message"]
                print(f"message: {message['content']!r}", flush=True)
                extra = {"task": message["content"][:120]} if verdict == "accepted" else {}
                await call(session, "reply", review_id=review_id, message_id=message["id"],
                           message_version=message["version"], verdict=verdict, author=MODEL,
                           content=f"Scripted {verdict}.", **extra)
            elif event["kind"] == "closure":
                closure = event.get("closure") or {}
        await call(session, "acknowledge_feedback", review_id=review_id, batch_id=batch["id"])
        if closure is not None:
            print(f"closure: {closure}", flush=True)
            return closure


async def main(args: argparse.Namespace) -> None:
    scenario_dir = Path(args.scenario).parent
    scenario = json.loads(Path(args.scenario).read_text())
    repo, work = Path(args.repo), Path(args.work)

    brief = work / "brief.md"
    brief.write_text(f"# Design brief\n\n{scenario['prompt']}\n\nBuild path: Direct.\n")
    run(sys.executable, SKILLS / "presentation/lib/presentation.py", "open", brief, cwd=repo)
    async with server("presentation") as session:
        connected = await call(session, "connect_review", doc=str(brief))
        await review(session, connected["review_id"], "answered")

    closure: dict | None = None
    for round_dir in scenario["fake"]["rounds"]:
        source = scenario_dir / round_dir
        changed = [p.relative_to(source) for p in source.rglob("*") if p.is_file()]
        for path in changed:
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source / path, repo / path)
        run(SKILLS / "_toolkit/lib/ledger.py", "author", "--repo", repo, "--model", MODEL, *changed, cwd=repo)
        run(SKILLS / "tmux/lib/tmux_pane.py", "open", "--cwd", repo, "--window", "review", "--env", "EDITOR",
            "--env", "VISUAL", "--expect", "tuicr", "--", SKILLS / "tuicr/lib/tuicr_up.py", repo, cwd=repo)
        async with server("tuicr") as session:
            connected = await call(session, "connect_review", repo=str(repo))
            closure = await review(session, connected["review_id"], "accepted")
        if closure.get("verdict") == "approved":
            break
    if not closure or closure.get("verdict") != "approved":
        raise SystemExit(f"never approved: {closure}")

    run(SKILLS / "_toolkit/lib/ledger.py", "approve", "--repo", repo, "--report", closure["report"], cwd=repo)
    flags = run(SKILLS / "_toolkit/lib/ledger.py", "commit-flags", "--repo", repo, cwd=repo)
    authors = [line.split("=", 1)[1] for line in flags.splitlines() if line.startswith("AUTHOR_MODEL=")]
    run(SKILLS / "git-commit/commit.py", "--description", "Add slugify", "--confirm-reviewed",
        *[f"--author-model={a}" for a in authors], cwd=repo)
    run(SKILLS / "_toolkit/lib/ledger.py", "committed", "--repo", repo, cwd=repo)
    run("git", "push", cwd=repo)
    print("FAKE_AGENT_DONE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--work", required=True)
    asyncio.run(main(parser.parse_args()))
