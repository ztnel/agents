#!/usr/bin/env python3
"""End-to-end developer-workflow run against a scripted human, judged by deterministic assertions.

    run_e2e.py --agent fake                       # protocol tier: no model, runs on every PR
    run_e2e.py --agent copilot --model gpt-6-luna # live tier: a real developer agent

Each run gets a fresh fixture repo with a bare origin, its own XDG state and
data roots and a private tmux server, so it never touches the caller's reviews,
ledger or windows. Watch a live run with ``tmux -L <socket> attach``.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import sandbox  # noqa: E402
from human import Human, Tmux  # noqa: E402

AGENT_WINDOW = "agent"
NUDGES = 2
IDLE_NUDGE = 120
TUICR_SESSIONS = Path.home() / "Library/Application Support/tuicr/reviews"


def agent_command(args: argparse.Namespace, scenario_path: Path, box: sandbox.Sandbox, work: Path) -> list[str]:
    if args.agent == "fake":
        return [sys.executable, str(HERE / "fake_agent.py"), "--scenario", str(scenario_path),
                "--repo", str(box.repo), "--work", str(work)]
    prompt = json.loads(scenario_path.read_text())["prompt"]
    return ["copilot", "--agent", "agents:developer", "--model", args.model, "--allow-all-tools",
            "--allow-all-paths", "--plugin-dir", str(ROOT), "-i", prompt]


def forget_tuicr_sessions(root: Path) -> None:
    """macOS tuicr ignores XDG; drop the sessions this run left in the real store."""
    sessions = TUICR_SESSIONS / "sessions"
    for path in sessions.glob("*.json"):
        if str(root) in path.read_text(errors="ignore"):
            path.unlink()
    index = TUICR_SESSIONS / "index.json"
    if index.exists():
        data = json.loads(index.read_text())
        data["entries"] = {k: v for k, v in data.get("entries", {}).items() if str(root) not in json.dumps(v)}
        index.write_text(json.dumps(data, indent=2))


def check(scenario: dict, box: sandbox.Sandbox, human: Human, outcome: dict, work: Path) -> list[str]:
    """Deterministic oracle; returns the failed assertions."""
    expect, failures = scenario["expect"], []
    commits = box.pushed()
    if len(commits) != expect["commits"]:
        failures.append(f"expected {expect['commits']} pushed commit(s), found {len(commits)}")
    if commits:
        paths = box.git("--git-dir", str(box.origin), "diff", "--name-only", box.base, commits[0]).split()
        stray = [p for p in paths if not any(fnmatch.fnmatch(p, g) for g in expect["paths"])]
        if stray:
            failures.append(f"unexpected paths committed: {stray}")
        message = box.git("--git-dir", str(box.origin), "log", "-1", "--format=%B", commits[0])
        for trailer in ("Co-authored-by:", f"Approved-by: {sandbox.HUMAN[0]} <{sandbox.HUMAN[1]}>"):
            if trailer not in message:
                failures.append(f"commit lacks trailer {trailer!r}")
        if outcome["model"] not in message:
            failures.append(f"commit does not credit {outcome['model']}")
        clone = work / "verify"
        box.git("clone", "-q", str(box.origin), str(clone), cwd=work)
        shutil.copy(HERE / "scenarios" / expect["acceptance"], clone / "tests" / "e2e_acceptance.py")
        result = subprocess.run(["uvx", "-q", "pytest", "-q", "tests/e2e_acceptance.py"], cwd=clone,
                                capture_output=True, text=True)
        outcome["acceptance"] = result.stdout.strip().splitlines()[-1:] or result.stderr[-300:]
        if result.returncode:
            failures.append(f"hidden acceptance failed: {result.stdout[-600:]}")
    rounds = human.report["tuicr_rounds"]
    if len(rounds) < len(scenario["tuicr"]):
        failures.append(f"expected {len(scenario['tuicr'])} tuicr rounds, saw {len(rounds)}")
    if any(not r.get("quit") for r in rounds):
        failures.append("tuicr did not close cleanly every round")
    if any("comment" in s for s in scenario["tuicr"][:1]) and rounds and "reply" not in rounds[0]:
        failures.append("agent never replied to the tuicr comment")
    if human.report["unanswered"]:
        failures.append(f"unanswered comments: {human.report['unanswered']}")
    if not human.report["presentation_closed"]:
        failures.append("design brief was never presented and agreed")
    if outcome["nudges"]:
        failures.append(f"agent stalled and needed {outcome['nudges']} nudge(s)")
    if outcome["timed_out"]:
        failures.append("run timed out")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="slugify")
    parser.add_argument("--agent", choices=("fake", "copilot"), default="fake")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--artifacts", type=Path, help="Copy logs, reports and the result here.")
    parser.add_argument("--keep", action="store_true", help="Keep the temp root and tmux server.")
    args = parser.parse_args()

    scenario_path = HERE / "scenarios" / f"{args.scenario}.json"
    scenario = json.loads(scenario_path.read_text())
    root = Path(tempfile.mkdtemp(prefix="agents-e2e-")).resolve()
    work = root / "work"
    work.mkdir()
    box = sandbox.create(scenario["fixture"], root)
    log_path = work / "human.log"
    log_file = log_path.open("a", buffering=1)

    def log(message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        log_file.write(line + "\n")
        print(line, flush=True)

    env = {**os.environ, "XDG_STATE_HOME": str(root / "state"), "XDG_DATA_HOME": str(root / "data"),
           "BROWSER": "true"}
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    os.environ.update({k: env[k] for k in ("XDG_STATE_HOME", "XDG_DATA_HOME")})
    tmux = Tmux(f"agents-e2e-{os.getpid()}")
    agent_log = work / "agent.log"
    command = shlex.join(agent_command(args, scenario_path, box, work))
    if args.agent == "fake":  # copilot needs the pane's TTY; its own session log is the record
        command += f" 2>&1 | tee {shlex.quote(str(agent_log))}"
    subprocess.run(["tmux", "-L", tmux.socket, "-f", "/dev/null", "new-session", "-d", "-s", "e2e", "-x", "200",
                    "-y", "50", "-n", AGENT_WINDOW, "-c", str(box.repo),
                    command], env=env, check=True)
    tmux.run("set-option", "-t", f"e2e:{AGENT_WINDOW}", "remain-on-exit", "on")
    log(f"run {args.agent}/{args.model if args.agent == 'copilot' else 'scripted'} in {root}; "
        f"watch: tmux -L {tmux.socket} attach")

    human = Human(scenario, box.repo, root / "state", tmux, AGENT_WINDOW, log)
    model = args.model if args.agent == "copilot" else "e2e-fake-agent"
    outcome = {"agent": args.agent, "model": model, "nudges": 0, "timed_out": False}
    start, idle_since = time.monotonic(), None
    started = time.time()
    failures = ["harness raised before the oracle ran"]
    deadline = start + scenario["expect"]["max_minutes"] * 60
    try:
        while time.monotonic() < deadline:
            try:
                human.step()
            except Exception as exc:  # noqa: BLE001 - keep driving; the oracle judges the outcome
                log(f"HUMAN ERROR {type(exc).__name__}: {exc}")
            pane = next((p for p in tmux.panes() if p["window"] == AGENT_WINDOW), None)
            if pane is None or pane["dead"] == "1":
                log("agent exited")
                break
            if args.agent == "copilot":
                screen = tmux.screen(pane["id"])
                if "Do you trust the files in this folder?" in screen:  # fresh temp repo every run
                    tmux.keys(pane["id"], "Enter")
                    continue
                busy = "interrupt" in screen
                idle_since = None if busy else (idle_since or time.monotonic())
                idle = time.monotonic() - idle_since if idle_since else 0
                if box.pushed() and idle > 15:
                    log("agent pushed and is idle")
                    break
                if idle > IDLE_NUDGE and not human.active():
                    if outcome["nudges"] >= NUDGES:
                        log("agent stalled; giving up")
                        break
                    outcome["nudges"] += 1
                    log(f"STALL {outcome['nudges']}: agent idle {IDLE_NUDGE}s with work outstanding; nudging")
                    tmux.keys(pane["id"], "continue", "Enter")
                    idle_since = None
            time.sleep(2)
        else:
            outcome["timed_out"] = True
        outcome["minutes"] = round((time.monotonic() - start) / 60, 1)
        outcome["human"] = human.report
        failures = check(scenario, box, human, outcome, work)
    finally:
        log_file.close()
        if args.artifacts:
            args.artifacts.mkdir(parents=True, exist_ok=True)
            for path in [log_path, agent_log, *(root / "state" / "tuicr" / "reviews").glob("*.json")]:
                if path.exists():
                    shutil.copy(path, args.artifacts / path.name)
            if args.agent == "copilot":
                sessions = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot")) / "session-state"
                runs = [d for d in sessions.glob("*/") if (d / "events.jsonl").exists()
                        and (d / "events.jsonl").stat().st_mtime >= started]
                if runs:
                    shutil.copy(max(runs, key=lambda d: d.stat().st_mtime) / "events.jsonl",
                                args.artifacts / "copilot-events.jsonl")
        if not args.keep:
            tmux.run("kill-server")
            forget_tuicr_sessions(root)
            shutil.rmtree(root, ignore_errors=True)
    outcome["failures"] = failures
    if args.artifacts:
        (args.artifacts / "result.json").write_text(json.dumps(outcome, indent=2))
    print(json.dumps(outcome, indent=2))
    print("E2E_OK" if not failures else "E2E_FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
