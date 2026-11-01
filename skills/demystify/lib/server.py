"""Loopback, token-protected HTTP server for one demystify session.

One process serves one Markdown explainer. The browser long-polls ``/api/state``
for document and conversation changes; agents long-poll ``/agent/wait`` for
human comments. Conversations persist as JSON beside the session metadata, so
reopening the same document resumes them.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import document

ASSETS = Path(__file__).resolve().parents[1] / "assets"
STATIC = {"/app.js": "text/javascript", "/style.css": "text/css", "/favicon.svg": "image/svg+xml",
          "/widget.js": "text/javascript", "/widget.css": "text/css"}
MAX_BODY = 256 * 1024
MAX_MESSAGE = 8000
MAX_EXCERPT = 2000
MAX_STATE = 2000
WAIT_MAX = 300
STATE_WAIT = 25
WORKING_WINDOW = 600
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
# Widgets run agent-authored scripts in an opaque-origin sandbox: no network, no access to the page,
# its token or the API. Origins are explicit because 'self' is ambiguous inside a sandbox.
WIDGET_CSP = ("default-src 'none'; script-src {o} 'unsafe-inline'; style-src {o} 'unsafe-inline'; "
              "img-src data: blob:; frame-ancestors {o}; base-uri 'none'; form-action 'none'; sandbox allow-scripts")
WIDGET_PAGE = ('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/widget.css">'
               '<script src="/widget.js"></script></head><body>\n{body}\n</body></html>')


class RequestError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"{name} must be a non-empty string")
    if len(value) > MAX_MESSAGE:
        raise RequestError(f"{name} exceeds {MAX_MESSAGE} characters")
    return value.strip()


class Session:
    """Document, threads and agent presence, guarded by one condition variable."""

    def __init__(self, doc: Path, store: Path) -> None:
        self.doc, self.store = doc, store
        self.cond = threading.Condition()
        self.version = 0
        self.mtime: float | None = None
        self.rendered = document.Rendered("", "", [])
        self.error = ""
        self.waiters: dict[str, int] = {}
        self.last_agent: dict | None = None
        data = json.loads(store.read_text()) if store.exists() else {}
        self.threads: list[dict] = data.get("threads", [])
        self.closed = False
        self.reload()

    # -- persistence -------------------------------------------------------
    def _save(self) -> None:
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps({"threads": self.threads, "closed": self.closed}, indent=1))
        tmp.replace(self.store)

    def _bump(self) -> None:
        self.version += 1
        self.cond.notify_all()

    # -- document ----------------------------------------------------------
    def reload(self) -> bool:
        """Re-render when the document's mtime changes. Returns whether it did."""
        try:
            mtime = self.doc.stat().st_mtime_ns
            if mtime == self.mtime:
                return False
            rendered = document.render(self.doc.read_text(encoding="utf-8"))
            error = ""
        except (OSError, UnicodeDecodeError) as exc:
            mtime, rendered, error = None, self.rendered, f"cannot read {self.doc}: {exc}"
            if error == self.error:
                return False
        with self.cond:
            self.mtime, self.rendered, self.error = mtime, rendered, error
            for thread in self.threads:
                thread["anchor"] = document.relocate(thread["anchor"], rendered.units)
            self._save()
            self._bump()
        return True

    def watch(self, stop: threading.Event) -> None:
        while not stop.wait(0.4):
            self.reload()

    # -- conversation ------------------------------------------------------
    def _thread(self, thread_id: str) -> dict:
        for thread in self.threads:
            if thread["id"] == thread_id:
                return thread
        raise RequestError(f"unknown thread {thread_id}")

    def _anchor(self, body: dict) -> dict:
        if body.get("document"):
            return {"start": 0, "end": 0, "quote": [], "outdated": False}
        try:
            start, end = int(body["start"]), int(body["end"])
        except (KeyError, TypeError, ValueError):
            raise RequestError("anchor needs integer start and end source lines") from None
        start, end = min(start, end), max(start, end)
        units = [u for u in self.rendered.units if start <= u.line <= end]
        if not units:
            raise RequestError(f"no commentable content on lines {start}-{end}")
        anchor = {"start": units[0].line, "end": units[-1].line, "quote": [u.text for u in units], "outdated": False}
        element = body.get("element")
        excerpt = body.get("excerpt")
        if element:
            if element not in units[0].labels:
                raise RequestError(f"diagram element {element!r} not found on line {start}")
            anchor["element"] = element
        state = body.get("state")
        if state is not None:
            if units[0].kind != "widget" or not isinstance(state, dict) or len(json.dumps(state)) > MAX_STATE:
                raise RequestError(f"state must be a widget's control values of at most {MAX_STATE} characters")
            anchor["state"] = state
        if excerpt:
            if not isinstance(excerpt, str) or len(excerpt) > MAX_EXCERPT:
                raise RequestError(f"excerpt must be a string of at most {MAX_EXCERPT} characters")
            squash = lambda text: "".join(text.split())  # noqa: E731 - rendering may differ in whitespace only
            if not squash(excerpt) or squash(excerpt) not in squash("".join(u.content for u in units)):
                raise RequestError(f"excerpt not found on lines {anchor['start']}-{anchor['end']}")
            anchor["excerpt"] = " ".join(excerpt.split())
            col = body.get("col", 0)
            anchor["col"] = col if isinstance(col, int) and col >= 0 else 0
        return anchor

    def _message(self, role: str, content: str, model: str | None = None) -> dict:
        msg = {"id": uuid.uuid4().hex[:12], "role": role, "content": content, "at": now()}
        if model:
            msg["model"] = model
        return msg

    def post(self, body: dict, role: str, model: str | None = None) -> dict:
        with self.cond:
            if self.closed:
                raise RequestError("session is closed")
            content = _text(body.get("content"), "content")
            if body.get("thread"):
                thread = self._thread(body["thread"])
            else:
                thread = {"id": uuid.uuid4().hex[:12], "anchor": self._anchor(body), "messages": []}
                self.threads.append(thread)
            message = self._message(role, content, model)
            thread["messages"].append(message)
            if role == "agent":
                self._seen(model, "working")
            self._save()
            self._bump()
            return {"thread": thread["id"], "message": message["id"]}

    def pending(self) -> list[dict]:
        """Threads whose latest message is from the human, with the unanswered tail."""
        result = []
        for thread in self.threads:
            tail = []
            for message in reversed(thread["messages"]):
                if message["role"] != "human":
                    break
                tail.insert(0, message)
            if tail:
                result.append({"thread": thread["id"], "anchor": thread["anchor"],
                               "context": self.context(thread["anchor"]), "messages": tail})
        return result

    def context(self, anchor: dict) -> str:
        if not anchor.get("start"):
            return "(whole document)"
        units = [u for u in self.rendered.units if anchor["start"] <= u.line <= anchor["end"]]
        if anchor.get("outdated"):
            text = "\n".join(anchor.get("quote", []))
        else:
            text = "\n".join(u.text for u in units)
            text += "".join(f"\n(LaTeX source, line {u.line}: {u.tex})" for u in units if u.tex)
        if anchor.get("element"):
            text += f"\n(diagram element: {anchor['element']})"
        if anchor.get("state"):
            values = ", ".join(f"{k}={v if isinstance(v, str) else json.dumps(v)}" for k, v in anchor["state"].items())
            text += f"\n(widget state when commented: {values})"
        if anchor.get("excerpt"):
            text += f"\n(selected text: \"{anchor['excerpt']}\")"
        return text

    # -- agent presence ----------------------------------------------------
    def _seen(self, model: str | None, state: str) -> None:
        if model:
            self.last_agent = {"model": model, "state": state, "at": time.time()}

    def agent_status(self) -> dict:
        if self.waiters:
            return {"state": "waiting", "models": sorted(self.waiters)}
        if self.last_agent and time.time() - self.last_agent["at"] < WORKING_WINDOW:
            return {"state": "working", "models": [self.last_agent["model"]]}
        return {"state": "offline", "models": [self.last_agent["model"]] if self.last_agent else []}

    def wait(self, model: str, timeout: float) -> dict:
        deadline = time.monotonic() + max(1.0, min(float(timeout), WAIT_MAX))
        with self.cond:
            self.waiters[model] = self.waiters.get(model, 0) + 1
            self._seen(model, "waiting")
            self._bump()
            try:
                while True:
                    if self.closed:
                        return {"status": "closed", "pending": self.pending()}
                    pending = self.pending()
                    if pending:
                        return {"status": "feedback", "pending": pending}
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {"status": "timeout", "pending": []}
                    self.cond.wait(remaining)
            finally:
                self.waiters[model] -= 1
                if not self.waiters[model]:
                    del self.waiters[model]
                self._seen(model, "working")
                self._bump()

    def snapshot(self) -> dict:
        return {
            "version": self.version, "doc": str(self.doc), "title": self.rendered.title or self.doc.stem,
            "html": self.rendered.html, "units": [u.as_dict() for u in self.rendered.units],
            "threads": self.threads, "agent": self.agent_status(), "closed": self.closed, "error": self.error,
        }

    def read(self) -> dict:
        with self.cond:
            threads = [{**t, "context": self.context(t["anchor"])} for t in self.threads]
            return {"doc": str(self.doc), "title": self.rendered.title, "closed": self.closed,
                    "error": self.error, "threads": threads, "pending": self.pending()}

    def state(self, since: int | None) -> dict:
        deadline = time.monotonic() + STATE_WAIT
        with self.cond:
            while since == self.version and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"version": self.version, "unchanged": True}
                self.cond.wait(remaining)
            return self.snapshot()

    def close(self) -> None:
        with self.cond:
            self.closed = True
            self._save()
            self._bump()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session: Session, token: str, port: int = 0) -> None:
        self.session, self.token = session, token
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.stop = threading.Event()

    def shutdown_soon(self, delay: float = 1.5) -> None:
        def later() -> None:
            time.sleep(delay)
            self.stop.set()
            self.shutdown()
        threading.Thread(target=later, daemon=True).start()


def new_token() -> str:
    return secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, fmt, *args):  # quiet: the log file is for errors only
        if len(args) > 1 and str(args[1])[:1] in "45":
            super().log_message(fmt, *args)

    def _send(self, code: int, data: bytes, kind: str = "application/json", csp: str = CSP) -> None:
        self.send_response(code)
        for key, value in (("Content-Type", kind), ("Content-Length", str(len(data))),
                           ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                           ("Referrer-Policy", "no-referrer"), ("Content-Security-Policy", csp)):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode())

    def _allowed(self, auth: bool) -> bool:
        host_ok = self.headers.get("Host") == urlsplit(self.server.origin).netloc
        origin = self.headers.get("Origin")
        if not host_ok or (origin and origin != self.server.origin):
            self._json(403, {"error": "invalid Host or Origin"})
            return False
        if auth and not hmac.compare_digest(self.headers.get("X-Token", ""), self.server.token):
            self._json(403, {"error": "invalid token"})
            return False
        return True

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY or self.headers.get("Content-Type") != "application/json":
            raise RequestError("expected a JSON body")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise RequestError("JSON body must be an object")
        return body

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        route, query = url.path, parse_qs(url.query)
        if not self._allowed(auth=route.startswith(("/api/", "/agent/"))):
            return
        session = self.server.session
        try:
            if route == "/":
                self._send(200, (ASSETS / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif route in STATIC:
                self._send(200, (ASSETS / route[1:]).read_bytes(), f"{STATIC[route]}; charset=utf-8")
            elif route.startswith("/widget/") and route[8:] in session.rendered.widgets:
                page = WIDGET_PAGE.format(body=session.rendered.widgets[route[8:]])
                self._send(200, page.encode(), "text/html; charset=utf-8", WIDGET_CSP.format(o=self.server.origin))
            elif route == "/health":
                self._json(200, {"ok": True, "doc": str(session.doc)})
            elif route == "/api/state":
                since = query.get("since", [None])[0]
                self._json(200, session.state(int(since) if since not in (None, "") else None))
            elif route == "/agent/read":
                self._json(200, session.read())
            elif route == "/agent/wait":
                model = _text(query.get("model", [""])[0], "model")
                self._json(200, session.wait(model, float(query.get("timeout", ["120"])[0])))
            else:
                self._json(404, {"error": "not found"})
        except (RequestError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        if not self._allowed(auth=True):
            return
        session = self.server.session
        try:
            body = self._body()
            if route == "/api/comment":
                self._json(200, session.post(body, "human"))
            elif route == "/agent/post":
                model = _text(body.get("model"), "model")
                self._json(200, session.post(body, "agent", model))
            elif route in ("/api/close", "/agent/close"):
                session.close()
                self.server.shutdown_soon()
                self._json(200, {"closed": True})
            else:
                self._json(404, {"error": "not found"})
        except (RequestError, ValueError) as exc:
            self._json(400, {"error": str(exc)})


def serve(doc: Path, store: Path, token: str, port: int = 0, ready=None) -> Server:
    """Build a server and its document watcher; the caller runs ``serve_forever``."""
    session = Session(doc, store)
    server = Server(session, token, port)
    threading.Thread(target=session.watch, args=(server.stop,), daemon=True).start()
    if ready:
        ready(server)
    return server
