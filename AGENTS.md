# Agents Repository Guidance

## Design principles

- Prefer executable code over prose when defining repeatable processes. Use prose to explain intent, interfaces, and constraints that code cannot express; keep it concise. Each skill must be a standalone capability and must not depend on another skill's files or implementation.
- Use custom agents to compose skills into constrained workflows. Keep workflow orchestration and cross-capability policy out of individual skills.
- Keep guardrails and process restrictions to the minimum required for safety and correctness. Otherwise, preserve agent flexibility and rely on best judgment.

## Release workflow

1. Update `plugin.json.version`.
2. Validate the manifest and skills.
3. Commit the reviewed changes.
4. Tag the release, for example `v0.1.0`.
5. Deploy from that tag using the GitHub Copilot CLI plugin installer.
