# Agents Repository Guidance

## Design principles

- Prefer executable code over prose when defining repeatable processes. Use prose to explain intent, interfaces, and constraints that code cannot express; keep it concise. Each skill must be a standalone capability and must not depend on another skill's files or implementation.
- Use custom agents to compose skills into constrained workflows. Keep workflow orchestration and cross-capability policy out of individual skills.
- Keep guardrails and process restrictions to the minimum required for safety and correctness. Otherwise, preserve agent flexibility and rely on best judgment.
- Maintain the source-controlled system megadrawing in `docs/INFRASTRUCTURE.md` whenever infrastructure changes. Keep skills, MCP servers, custom agents, their relationships, and policy-enforcement boundaries current in the same change; distinguish implemented behavior from proposals and prose contracts from executable checks.
- Never keep backwards compatibility with older versions of external dependencies. When an upgrade breaks behavior, support only the new version. When an agent requests a feature the installed version lacks, emit a Python warning telling the human to upgrade that dependency instead of adding a fallback.

## Sandbox feedback loop

Validate changes to skills, agents or feedback servers end to end, not only with unit tests:

1. Build a throwaway repository with a small feature request and a local bare remote.
2. Run the affected custom agent there with a weaker model, loading this checkout through `copilot --plugin-dir <checkout>`. Weaker models expose ambiguous prose, hidden dependencies and missing next steps that stronger models work around.
3. Act as the human through the real review surfaces, and observe the run from outside it (session events, tool calls, processes).
4. Turn each failure into a fix, preferring code (tool results, checks, lint rules) over prose, and add a regression test where possible.
5. Reset the sandbox and its review state, then re-run until the fixes hold.

## Release workflow

1. Update `plugin.json.version`.
2. Validate the manifest and skills.
3. Commit the reviewed changes.
4. Tag the release, for example `v0.1.0`.
5. Deploy from that tag using the GitHub Copilot CLI plugin installer.
