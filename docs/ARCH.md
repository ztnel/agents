# Architecture

## Package layout

The repository uses [Agent Plugins 1.0.0](https://agent-plugins.org/specification).
The schema identifier in [`plugin.json`](../plugin.json) pins the package format
to exactly `1.0.0`; the manifest's `version` tracks plugin releases separately.

| Path | Responsibility |
| --- | --- |
| `plugin.json` | Plugin manifest and Copilot extension registration |
| `mcp.json` | Official GitHub MCP endpoint, scoped to `repos,issues` |
| `skills/<name>/SKILL.md` | Discoverable skill contract and activation description |
| `skills/_lib/skillkit/` | Shared Python primitives for processes, paths, Git, tmux, and review sessions |
| `com.github.copilot/agents/` | Copilot CLI custom agents that compose skills into workflows |
| `tests/` | Repository regression and package-discovery harnesses |

## Design criteria

**Copilot CLI is the supported client.** Installation and runtime behavior are
designed for it; cross-client compatibility is not tested or maintained. Custom
agents use the `com.github.copilot` reverse-domain extension namespace because
Agent Plugins 1.0.0 does not define a core custom-agent component.

**Skills own capabilities; agents own orchestration.** Each skill is standalone
and must not depend on another skill's implementation. Custom agents compose
skills and own cross-capability workflow policy.

**Prefer executable processes to procedural prose.** Python scripts implement
repeatable behavior; documentation explains intent, interfaces, and constraints.
Reuse shared primitives in [`skillkit`](../skills/_lib/README.md) rather than
duplicating platform-specific logic.

**Keep guardrails focused.** Require restrictions needed for safety and
correctness; otherwise preserve agent flexibility and judgment. Local review
workflows keep human approval explicit, with staging, commits, and remote
publication outside the tuicr watch loop.

See [CI and validation](CI.md) for the checks covering these surfaces.
