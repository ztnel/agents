#!/usr/bin/env python3
"""demystify — open an explainer page and converse with the human on it.

    demystify.py open <doc.md> [--no-browser | --browser] [--restart]
    demystify.py wait  <doc.md> --model M [--timeout 120]
    demystify.py read  <doc.md>
    demystify.py reply <doc.md> --model M --thread T (--body TEXT | --body-file F)
    demystify.py ask   <doc.md> --model M (--line N [--end N] [--element L | --text T] | --document) --body TEXT
    demystify.py close <doc.md>
    demystify.py list

Outputs JSON on stdout. ``open`` reuses a running server for the same document and opens a browser tab
only when it starts a server; ``--browser`` reopens one.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import paths, proc  # noqa: E402
from skillkit.cli import run_main  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402

import server  # noqa: E402

STATE = ("demystify",)


def session_dir(doc: Path, create: bool = False) -> Path:
    return paths.state_dir(*STATE, paths.short_hash(str(doc)), create=create)


def resolve(doc: str) -> Path:
    path = Path(doc).expanduser().resolve()
    if path.suffix.lower() not in (".md", ".markdown"):
        raise UsageError(f"{path} is not a Markdown file")
    return path


def load(doc: Path) -> dict | None:
    meta = paths.read_json(session_dir(doc) / "session.json")
    return meta if isinstance(meta, dict) and meta.get("port") else None


def call(meta: dict, route: str, body: dict | None = None, timeout: float = 10) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{meta['port']}{route}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Token": meta["token"], "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = json.loads(exc.read() or b"{}").get("error", exc.reason)
        raise SkillError(f"server rejected {route}: {detail}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise SkillError(f"no demystify server for this document ({exc})",
                         code=4, hint="run: demystify.py open <doc.md>") from None


def healthy(meta: dict | None) -> bool:
    if not meta:
        return False
    try:
        call(meta, "/health", timeout=2)
        return True
    except SkillError:
        return False


def running(doc: Path) -> dict:
    meta = load(doc)
    if not healthy(meta):
        raise SkillError(f"no demystify server for {doc}", code=4, hint="run: demystify.py open <doc.md>")
    return meta


def url(meta: dict) -> str:
    return f"http://127.0.0.1:{meta['port']}/#t={meta['token']}"


def cmd_open(args) -> dict:
    doc = resolve(args.doc)
    if not doc.is_file():
        raise UsageError(f"{doc} does not exist")
    directory = session_dir(doc, create=True)
    with open(directory / "open.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        meta = load(doc)
        reused = healthy(meta)
        previous = None
        if reused and args.restart:
            # Hand the port and token to the new server privately so open tabs reconnect.
            previous = {"port": meta["port"], "token": meta["token"]}
            if not proc.terminate(meta["pid"]):
                raise SkillError(f"cannot stop server pid {meta['pid']}")
            handoff = directory / "resume.json"
            handoff.write_text(json.dumps(previous))
            os.chmod(handoff, 0o600)
            reused = False
        if not reused:
            (directory / "session.json").unlink(missing_ok=True)
            pid = proc.detach([sys.executable, str(Path(__file__).resolve()), "serve", str(doc)],
                              directory / "server.log")
            deadline = time.monotonic() + 10
            while not ((meta := load(doc)) and meta.get("pid") == pid and healthy(meta)):
                if time.monotonic() > deadline or not proc.pid_alive(pid):
                    proc.terminate(pid)
                    raise SkillError(f"server failed to start; see {directory / 'server.log'}")
                time.sleep(0.1)
    reconnects = previous is not None and previous["port"] == meta["port"]
    if args.browser or not (reused or reconnects or args.no_browser):
        webbrowser.open(url(meta))
    return {"doc": str(doc), "url": url(meta), "reused": reused, "pid": meta["pid"]}


def cmd_serve(args) -> None:
    doc = resolve(args.doc)
    directory = session_dir(doc, create=True)
    token, port = server.new_token(), 0
    handoff = directory / "resume.json"
    if handoff.is_file():
        resume = json.loads(handoff.read_text())
        handoff.unlink()
        token, port = resume["token"], resume["port"]

    def ready(srv: server.Server) -> None:
        meta = {"doc": str(doc), "port": srv.server_port, "token": token, "pid": os.getpid()}
        target = directory / "session.json"
        tmp = directory / f"session.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(meta))
        os.chmod(tmp, 0o600)
        tmp.replace(target)

    try:
        srv = server.serve(doc, directory / "threads.json", token, port, ready=ready)
    except OSError:  # the old port was taken meanwhile; open will start a new tab
        token = server.new_token()
        srv = server.serve(doc, directory / "threads.json", token, ready=ready)
    try:
        srv.serve_forever()
    finally:
        meta = load(doc)
        if meta and meta.get("pid") == os.getpid():
            (directory / "session.json").unlink(missing_ok=True)


def body_text(args) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    if args.body:
        return args.body
    raise UsageError("provide --body or --body-file")


def cmd_wait(args) -> dict:
    doc = resolve(args.doc)
    meta = load(doc)
    if not healthy(meta):
        store = paths.read_json(session_dir(doc) / "threads.json")
        if isinstance(store, dict) and store.get("closed"):
            return {"status": "closed", "pending": []}
        meta = running(doc)
    query = urllib.parse.urlencode({"model": args.model, "timeout": args.timeout})
    return call(meta, f"/agent/wait?{query}", timeout=args.timeout + 15)


def cmd_read(args) -> dict:
    return call(running(resolve(args.doc)), "/agent/read")


def cmd_reply(args) -> dict:
    return call(running(resolve(args.doc)), "/agent/post",
                {"thread": args.thread, "model": args.model, "content": body_text(args)})


def cmd_ask(args) -> dict:
    body = {"model": args.model, "content": body_text(args)}
    if args.document:
        body["document"] = True
    elif args.line:
        body.update(start=args.line, end=args.end or args.line, element=args.element, excerpt=args.text)
    else:
        raise UsageError("provide --line N or --document")
    return call(running(resolve(args.doc)), "/agent/post", body)


def cmd_close(args) -> dict:
    return call(running(resolve(args.doc)), "/agent/close", {})


def cmd_list(_args) -> dict:
    sessions = []
    root = paths.state_dir(*STATE)
    for meta_file in sorted(root.glob("*/session.json")) if root.is_dir() else []:
        meta = paths.read_json(meta_file)
        if isinstance(meta, dict) and healthy(meta):
            sessions.append({"doc": meta["doc"], "url": url(meta), "pid": meta["pid"]})
    return {"sessions": sessions}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="demystify.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("open", "serve", "wait", "read", "reply", "ask", "close"):
        s = sub.add_parser(name)
        s.add_argument("doc")
        if name == "open":
            browser = s.add_mutually_exclusive_group()
            browser.add_argument("--no-browser", action="store_true")
            browser.add_argument("--browser", action="store_true", help="open a tab even if the server is already running")
            s.add_argument("--restart", action="store_true",
                           help="replace a running server on the same port and token, so open tabs reconnect")
        if name in ("wait", "reply", "ask"):
            s.add_argument("--model", required=True, help="your concrete model id, e.g. claude-opus-4.8")
        if name == "wait":
            s.add_argument("--timeout", type=int, default=120)
        if name in ("reply", "ask"):
            s.add_argument("--body")
            s.add_argument("--body-file")
        if name == "reply":
            s.add_argument("--thread", required=True)
        if name == "ask":
            s.add_argument("--line", type=int)
            s.add_argument("--end", type=int)
            s.add_argument("--element")
            s.add_argument("--text", help="exact phrase on those lines to anchor to")
            s.add_argument("--document", action="store_true")
    sub.add_parser("list")
    return p


def main(argv) -> int:
    args = parser().parse_args(argv)
    result = globals()[f"cmd_{args.command}"](args)
    if result is not None:
        print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    run_main(main)
