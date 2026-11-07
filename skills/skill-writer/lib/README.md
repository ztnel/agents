# Skill writer `lib/` API

| Command | Contract | Exit codes |
|---|---|---|
| `skill_new.py <skill-name> [--root dir]` | Validates the name, creates `SKILL.md` and empty `lib/`, then runs the existing linter without overwriting a destination. | `2` usage/name, `1` destination/template failure. |
| `skill-lint.py [targets…] [--strict] [--json] [--list-names]` | Existing read-only specification and convention linter. | See `skill-lint.py --help`. |

## Design

The scaffolder only creates a newly requested skill and never commits. It uses
the shared Python primitives and direct template replacement rather than shell
`sed`, keeping generated skeletons portable to Linux, macOS, and WSL.
