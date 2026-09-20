#!/usr/bin/env python3
"""Deterministic repository regression harness for CI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SKILL_LINT = REPO_ROOT / "skills" / "skill-writer" / "lib" / "skill-lint.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PLUGIN_JSON = REPO_ROOT / "plugin.json"
MCP_JSON = REPO_ROOT / "mcp.json"

SKIP_DIRS = {".git", "__pycache__", ".copilot-home", ".pytest_cache", "node_modules"}


def _run(argv: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"\nFAILED: {' '.join(argv)}", file=sys.stderr)
        if proc.stdout:
            print("--- stdout ---", file=sys.stderr)
            print(proc.stdout, file=sys.stderr)
        if proc.stderr:
            print("--- stderr ---", file=sys.stderr)
            print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc


def _filtered_rglob(pattern: str) -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob(pattern):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS or part.startswith(".") and part != "." for part in path.relative_to(REPO_ROOT).parts):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(REPO_ROOT).as_posix())


def _check_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = [
        "on:",
        "pull_request:",
        "push:",
        "branches:",
        "- main",
        "permissions:",
        "contents: read",
        "concurrency:",
        "cancel-in-progress: true",
        "regression:",
        "smoke:",
        'run: echo "COPILOT_HOME=$RUNNER_TEMP/copilot-home" >> "$GITHUB_ENV"',
        "run: npm install -g @github/copilot@1.0.86",
    ]
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit(f"workflow check failed; missing tokens: {', '.join(missing)}")
    if 'COPILOT_HOME: ${{ runner.temp }}/copilot-home' in text:
        raise SystemExit("workflow check failed; job-level COPILOT_HOME must not use runner.temp")


def _check_manifests() -> None:
    plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    mcp = json.loads(MCP_JSON.read_text(encoding="utf-8"))

    if plugin.get("name") != "agents":
        raise SystemExit("plugin.json must name the package 'agents'")
    if not isinstance(plugin.get("version"), str) or not plugin["version"]:
        raise SystemExit("plugin.json must declare a version string")

    extensions = plugin.get("extensions", {})
    copilot = extensions.get("com.github.copilot", {})
    agents_path = copilot.get("agents")
    if agents_path != "./com.github.copilot/agents":
        raise SystemExit("plugin.json must point com.github.copilot.agents at ./com.github.copilot/agents")

    if mcp.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json":
        raise SystemExit("mcp.json schema mismatch")
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or "github" not in servers:
        raise SystemExit("mcp.json must declare the GitHub MCP server")
    github = servers["github"]
    if github.get("type") != "streamable-http":
        raise SystemExit("mcp.json github server must use streamable-http")
    if github.get("url") != "https://api.githubcopilot.com/mcp/":
        raise SystemExit("mcp.json github server URL mismatch")


def _compile_python() -> None:
    targets = [str(REPO_ROOT / "skills"), str(REPO_ROOT / "com.github.copilot"), str(REPO_ROOT / "tests")]
    _run([PYTHON, "-m", "compileall", "-q", *targets])


def _run_python_tests() -> None:
    files = _filtered_rglob("test_*.py")
    if not files:
        raise SystemExit("no python test_*.py files found")
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(REPO_ROOT)
        print(f"[{index}/{len(files)}] python {rel}")
        _run([PYTHON, str(path)])


def _run_shell_tests() -> None:
    files = sorted((REPO_ROOT / "skills" / "tuicr" / "tests").glob("test-*.sh"))
    if not files:
        raise SystemExit("no tuicr shell tests found")
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(REPO_ROOT)
        print(f"[{index}/{len(files)}] bash {rel}")
        _run(["bash", str(path)])


def _git_diff_check() -> None:
    _run(["git", "diff", "--check"], cwd=REPO_ROOT)


def main() -> int:
    os.chdir(REPO_ROOT)
    _compile_python()
    _check_workflow()
    _check_manifests()
    _run_python_tests()
    _run_shell_tests()
    _run([PYTHON, str(SKILL_LINT), str(REPO_ROOT / "skills" / "github-issue"), "--strict"])
    _run([PYTHON, str(SKILL_LINT), str(REPO_ROOT / "skills")])
    _git_diff_check()
    print("REGRESSION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
