"""Scripted human for e2e runs: plays the reviewer on the presentation page and in tuicr.

Presentation: posts over the server's token API, and only while an agent waits.
tuicr: comments via ``tuicr review add``; marks and quit via keys in the tuicr pane.
Behaviour comes from the scenario file, never from the agent under test.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

USER = "E2E Human"
SETTLE = 3.0


@dataclass
class Tmux:
    socket: str

    def run(self, *args: str, check: bool = False) -> str:
        return subprocess.run(["tmux", "-L", self.socket, *args], capture_output=True,
                              text=True, check=check).stdout

    def panes(self) -> list[dict]:
        out = self.run("list-panes", "-a", "-F", "#{pane_id}\t#{window_name}\t#{pane_dead}\t#{pane_current_path}")
        return [dict(zip(("id", "window", "dead", "path"), line.split("\t"))) for line in out.splitlines()]

    def keys(self, pane: str, *keys: str) -> None:
        self.run("send-keys", "-t", pane, *keys)

    def screen(self, pane: str) -> str:
        return self.run("capture-pane", "-p", "-t", pane)


@dataclass
class Human:
    scenario: dict
    repo: Path
    state: Path
    tmux: Tmux
    agent_window: str
    log: Callable[[str], None]
    pres: dict = field(default_factory=dict)
    round: int = 0
    in_round: bool = False
    report: dict = field(default_factory=lambda: {"presentation_posts": 0, "presentation_closed": 0,
                                                  "tuicr_rounds": [], "unanswered": []})

    # ---------- presentation ----------

    def _api(self, meta: dict, route: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{meta['port']}{route}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Token": meta["token"], "Content-Type": "application/json"},
            method="POST" if body is not None else "GET")
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - loopback only
            return json.loads(response.read())

    def presentation(self) -> None:
        script = self.scenario["presentation"]
        for path in (self.state / "demystify").glob("*/session.json"):
            try:
                meta = json.loads(path.read_text())
                state = self._api(meta, "/api/state")
            except (OSError, ValueError):
                continue
            rec = self.pres.setdefault(meta["pid"], {"posts": 0, "closed": False, "ready": None})
            if rec["closed"] or state["closed"]:
                rec["closed"] = True
                continue
            if state["agent"]["state"] != "waiting":
                rec["ready"] = None
                continue
            rec["ready"] = rec["ready"] or time.monotonic()
            if time.monotonic() - rec["ready"] < SETTLE:
                continue
            threads = state["threads"]
            if any(t["messages"] and t["messages"][-1]["role"] == "human" for t in threads):
                continue  # the agent still owes a reply
            asks = [t for t in threads if t["messages"] and t["messages"][-1]["role"] == "agent"
                    and (t["messages"][0]["role"] == "agent" or "?" in t["messages"][-1]["content"]
                         or "confirm" in t["messages"][-1]["content"].lower())
                    and sum(m["role"] == "human" for m in t["messages"]) < 2]
            if asks and rec["posts"] < 5:
                thread = asks[0]
                self.log(f"PRES answer: agent asked {thread['messages'][-1]['content'][:160]!r}")
                self._api(meta, "/api/comment", {"thread": thread["id"], "content": script["answer"]})
            elif rec["posts"] == 0:
                self.log(f"PRES comment on {Path(meta['doc']).name}")
                self._api(meta, "/api/comment", {"document": True, "content": script["comment"]})
            else:
                self.log(f"PRES close {Path(meta['doc']).name} after {rec['posts']} posts")
                self._api(meta, "/api/close", {})
                rec["closed"] = True
                self.report["presentation_closed"] += 1
                continue
            rec["posts"] += 1
            self.report["presentation_posts"] += 1
            rec["ready"] = None

    # ---------- tuicr ----------

    def _tuicr(self, *args: str) -> list | dict:
        out = subprocess.run(["tuicr", "review", *args, "--repo", str(self.repo)],
                             capture_output=True, text=True).stdout
        try:
            return json.loads(out or "[]")
        except json.JSONDecodeError:
            return []

    def active(self) -> dict | None:
        return next((s for s in self._tuicr("list") if s.get("active")), None)

    def tuicr_pane(self) -> str | None:
        for pane in self.tmux.panes():
            if (pane["window"] != self.agent_window and pane["dead"] != "1"
                    and self.repo.name in pane["path"] and "tuicr" in self.tmux.screen(pane["id"])[:400]):
                return pane["id"]
        return None

    def _quit(self, pane: str) -> bool:
        self.tmux.keys(pane, "Escape")
        self.tmux.keys(pane, ":wq", "Enter")
        for _ in range(15):
            time.sleep(0.7)
            if "Copy review to clipboard" in self.tmux.screen(pane):
                self.tmux.keys(pane, "n")
            if not self.active():
                self.log("TUICR quit")
                return True
        self.log("TUICR did not quit:\n" + "\n".join(self.tmux.screen(pane).splitlines()[-3:]))
        return False

    def _line(self, file: str, match: str) -> int:
        for number, line in enumerate((self.repo / file).read_text().splitlines(), 1):
            if match in line:
                return number
        return 1

    def tuicr(self) -> None:
        session, pane = self.active(), self.tuicr_pane()
        if not session or not pane:
            self.in_round = False
            return
        if self.in_round:
            return
        self.in_round = True
        steps = self.scenario["tuicr"]
        step = steps[min(self.round, len(steps) - 1)]
        self.round += 1
        record = {"round": self.round, "files": session["file_count"], "action": next(iter(step))}
        self.report["tuicr_rounds"].append(record)
        self.log(f"TUICR round {self.round}: {session['slug']} files={session['file_count']}")
        time.sleep(SETTLE * 2)
        slug = session["slug"]
        if "comment" in step:
            spec = step["comment"]
            line = self._line(spec["file"], spec["line_match"])
            added = self._tuicr("add", "--session", slug, "--target-file", spec["file"],
                                "--line", str(line), "--username", USER, spec["text"])
            mine = added.get("id") if isinstance(added, dict) else None
            self.log(f"TUICR comment {spec['file']}:{line} id={mine}")
            deadline = time.monotonic() + 360
            reply = None
            while time.monotonic() < deadline and reply is None:
                time.sleep(3)
                reply = next((c for c in self._tuicr("comments", "--session", slug)
                              if c.get("author") != USER and c.get("path") == spec["file"]), None)
            if reply:
                self.log(f"TUICR reply by {reply.get('author')}: {reply['content'][:200]!r}")
                record["reply"] = reply["content"]
            else:
                self.report["unanswered"].append(spec["text"])
                self.log("TUICR no reply within 6 min; closing anyway")
            time.sleep(SETTLE)
        else:
            for _ in range(int(session["file_count"])):
                self.tmux.keys(pane, "r")
                time.sleep(0.5)
                self.tmux.keys(pane, "}")
                time.sleep(0.5)
            time.sleep(1)
        record["quit"] = self._quit(pane)
        # A fast agent can reopen before the next poll ever sees the review closed.
        self.in_round = not record["quit"]

    def step(self) -> None:
        self.presentation()
        self.tuicr()
