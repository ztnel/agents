#!/usr/bin/env python3
"""Copilot package discovery and agent smoke checks."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
PLUGIN_JSON = REPO_ROOT / "plugin.json"
MCP_JSON = REPO_ROOT / "mcp.json"
AGENTS_DIR = REPO_ROOT / "com.github.copilot" / "agents"


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
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


@contextlib.contextmanager
def _copilot_home() -> Any:
    existing = os.environ.get("COPILOT_HOME")
    if existing:
        yield Path(existing)
        return
    temp_root = Path(os.environ["RUNNER_TEMP"]) if os.environ.get("RUNNER_TEMP") else REPO_ROOT.parent
    with tempfile.TemporaryDirectory(prefix="copilot-home-", dir=temp_root) as temp_dir:
        yield Path(temp_dir)


def _copilot(argv: list[str], *, env: dict[str, str], plugin_dir: Path | None) -> subprocess.CompletedProcess[str]:
    cmd = ["copilot", "--no-auto-update"]
    if plugin_dir is not None:
        cmd.extend(["--plugin-dir", str(plugin_dir)])
    cmd.extend(argv)
    return _run(cmd, env=env)


def _json_output(argv: list[str], *, env: dict[str, str], plugin_dir: Path | None) -> Any:
    proc = _copilot(argv, env=env, plugin_dir=plugin_dir)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"copilot {' '.join(argv)} did not return JSON: {exc}") from exc


def _agent_names() -> list[str]:
    names: list[str] = []
    for path in sorted(AGENTS_DIR.glob("*.agent.md"), key=lambda item: item.name):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise SystemExit(f"{path} is missing YAML frontmatter")
        end = text.find("\n---\n", 4)
        if end == -1:
            raise SystemExit(f"{path} frontmatter is unterminated")
        frontmatter = text[4:end]
        name = None
        for line in frontmatter.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
        if not name:
            raise SystemExit(f"{path} must declare a frontmatter name")
        names.append(name)
    return names


def _expected_skills() -> list[str]:
    skills_dir = REPO_ROOT / "skills"
    names: list[str] = []
    for path in sorted(skills_dir.iterdir(), key=lambda item: item.name):
        if not path.is_dir():
            continue
        if not (path / "SKILL.md").is_file():
            continue
        names.append(path.name)
    return names


def _check_plugin(plugin_list: list[dict[str, Any]], *, plugin_dir: Path | None) -> None:
    declared = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    plugin = next((item for item in plugin_list if item.get("name") == declared["name"]), None)
    if plugin is None:
        raise SystemExit(f"plugin {declared['name']!r} was not discovered by Copilot")
    expected_source = "external" if plugin_dir is not None else "installed"
    if plugin.get("source") != expected_source:
        raise SystemExit(
            f"plugin {declared['name']!r} source mismatch: expected {expected_source}, got {plugin.get('source')}"
        )
    if not plugin.get("enabled"):
        raise SystemExit(f"plugin {declared['name']!r} is not enabled")
    if plugin.get("version") != declared["version"]:
        raise SystemExit(
            f"plugin {declared['name']!r} version mismatch: expected {declared['version']}, got {plugin.get('version')}"
        )


def _check_skills(skill_list: list[dict[str, Any]], *, plugin_dir: Path | None) -> None:
    expected = set(_expected_skills())
    discovered = {item.get("name"): item for item in skill_list if item.get("source") == "plugin"}
    missing = sorted(name for name in expected if name not in discovered)
    if missing:
        raise SystemExit(f"missing plugin skills: {', '.join(missing)}")
    for name in sorted(expected):
        item = discovered[name]
        if not item.get("enabled"):
            raise SystemExit(f"skill {name!r} is not enabled")
        if item.get("source") != "plugin":
            raise SystemExit(f"skill {name!r} must be discovered from the plugin")


def _check_mcp(mcp_list: dict[str, Any], *, plugin_dir: Path | None) -> None:
    declared = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    runtime = mcp_list.get("mcpServers", {})
    if not runtime:
        flattened: dict[str, Any] = {}

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("type"), str) and isinstance(node.get("url"), str):
                    name = node.get("name") if isinstance(node.get("name"), str) else None
                    if name:
                        flattened[name] = node
                    return
                for key, value in node.items():
                    if isinstance(value, dict):
                        if isinstance(value.get("type"), str) and isinstance(value.get("url"), str):
                            flattened[key] = value
                        else:
                            collect(value)

        collect(mcp_list)
        runtime = flattened
    if plugin_dir is not None and not runtime:
        print("Skipping MCP discovery check in plugin-dir mode because Copilot did not surface plugin MCP servers.")
        return
    for name, expected in declared.get("mcpServers", {}).items():
        actual = runtime.get(name)
        if actual is None:
            raise SystemExit(f"missing MCP server {name!r}")
        if actual.get("source") != "plugin":
            raise SystemExit(f"MCP server {name!r} must come from the plugin")
        if not actual.get("enabled"):
            raise SystemExit(f"MCP server {name!r} is not enabled")
        if not _transport_matches(expected.get("type"), actual.get("type")):
            raise SystemExit(
                f"MCP server {name!r} type mismatch: expected {expected.get('type')!r}, got {actual.get('type')!r}"
            )
        if actual.get("url") != expected.get("url"):
            raise SystemExit(
                f"MCP server {name!r} url mismatch: expected {expected.get('url')!r}, got {actual.get('url')!r}"
            )


def _transport_matches(declared: object, runtime: object) -> bool:
    if declared == runtime:
        return True
    if not isinstance(declared, str) or not isinstance(runtime, str):
        return False
    return (
        (declared == "streamable-http" and runtime == "http")
        or (declared == "http" and runtime == "streamable-http")
    )


def _live_agent_env(home: Path) -> dict[str, str]:
    env = {**os.environ, "COPILOT_HOME": str(home)}
    return env


def _agent_argv(name: str, *, plugin_dir: Path | None) -> list[str]:
    prompt = "Reply exactly: AGENTS_SMOKE_OK"
    argv = [
        "copilot",
        "--no-auto-update",
        "--available-tools",
        "",
        "--no-ask-user",
        "--silent",
        "--secret-env-vars=COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN",
        "--agent",
        f"agents:{name}",
        "-p",
        prompt,
    ]
    if plugin_dir is not None:
        argv[1:1] = ["--plugin-dir", str(plugin_dir)]
    return argv


def _run_agent(name: str, *, plugin_dir: Path | None, home: Path) -> None:
    argv = _agent_argv(name, plugin_dir=plugin_dir)
    proc = _run(argv, env=_live_agent_env(home))
    if proc.stdout.strip() != "AGENTS_SMOKE_OK":
        raise SystemExit(
            f"agent agents:{name} returned unexpected output: {proc.stdout.strip()!r}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", type=Path, default=None)
    parser.add_argument("--skip-live", action="store_true")
    parser.add_argument("--expect-live", action="store_true")
    args = parser.parse_args(argv)

    if args.skip_live and args.expect_live:
        raise SystemExit("--skip-live and --expect-live are mutually exclusive")

    with _copilot_home() as home:
        env = {**os.environ, "COPILOT_HOME": str(home)}
        plugin_list = _json_output(["plugin", "list", "--json"], env=env, plugin_dir=args.plugin_dir)
        skill_list = _json_output(["skill", "list", "--json"], env=env, plugin_dir=args.plugin_dir)
        mcp_list = _json_output(["mcp", "list", "--json"], env=env, plugin_dir=args.plugin_dir)

        if not isinstance(plugin_list, list) or not isinstance(skill_list, list):
            raise SystemExit("Copilot JSON discovery returned unexpected structures")
        if not isinstance(mcp_list, dict):
            raise SystemExit("Copilot MCP discovery returned unexpected structure")

        _check_plugin(plugin_list, plugin_dir=args.plugin_dir)
        _check_skills(skill_list, plugin_dir=args.plugin_dir)
        _check_mcp(mcp_list, plugin_dir=args.plugin_dir)

        agent_names = _agent_names()
        if args.skip_live:
            print("Skipping live agent sessions by request.")
            print("SMOKE_OK")
            return 0

        token = os.environ.get("COPILOT_GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            if args.expect_live:
                raise SystemExit(
                    "COPILOT_GITHUB_TOKEN (or GH_TOKEN/GITHUB_TOKEN) is required for live agent sessions on trusted contexts"
                )
            print("Skipping live agent sessions because no Copilot auth token is set.")
            print("SMOKE_OK")
            return 0

        for name in agent_names:
            _run_agent(name, plugin_dir=args.plugin_dir, home=home)

        print("SMOKE_OK")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
