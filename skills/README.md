# skills

Agent Skills packaged for discovery by GitHub Copilot CLI.

## Public API

Each immediate child directory with a `SKILL.md` is a public skill. Shared support code lives in `_lib/`, and workflow support used by multiple skills lives in `_toolkit/`; neither directory is a skill because neither contains a `SKILL.md`.

## Design criteria

Skills preserve the existing local workflow contracts while using the Agent
Plugins `skills/` component location recognized by Copilot CLI. Keep reusable
Python helpers in `_lib/skillkit` instead of duplicating platform logic in
individual skills.
