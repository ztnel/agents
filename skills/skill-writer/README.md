# `skill-writer`

Author, validate and repair Agent Skills so they actually load.

A malformed skill fails silently — the runtime skips it and writes nothing to any log.
This skill provides the missing feedback loop: a deterministic linter, a
compliant-by-construction scaffolder, and a documented repair procedure.

Authority: the Agent Skills specification, <https://agentskills.io/specification>.

## Public API

### `lib/skill-lint.py`

Validates skills. Read-only; no network access.

```bash
lib/skill-lint.py [targets...] [--strict] [--json] [--list-names]
```

| Argument | Meaning |
|---|---|
| `targets` | Skill directories, or a directory containing skills. Default: `~/.agents/skills` |
| `--strict` | Treat warnings as failures. Use for new skills. |
| `--json` | Machine-readable output: per-skill findings plus a `totals` block. |
| `--list-names` | Print only the names of skills expected to load, one per line. |

**Exit codes**

| Code | Meaning |
|---|---|
| `0` | No errors (and no warnings, under `--strict`) |
| `1` | At least one error, or a warning under `--strict` |
| `2` | Bad usage — target is not a directory, or no `SKILL.md` found |

**Severity classes.** `ERROR` is a spec violation: the skill will not load, or it breaks
the published contract. `warn` is a convention or a spec recommendation: the skill
loads correctly. The two are deliberately never conflated.

Errors: missing or non-UTF-8 `SKILL.md`; UTF-8 BOM; missing or malformed `---`
delimiters; unsupported YAML frontmatter; duplicate keys; frontmatter that is not a mapping; a `name`
that is missing, over 64 characters, outside `[a-z0-9-]`, hyphen-bounded,
double-hyphenated or unequal to the directory name; a `description` that is missing,
empty, non-string or over 1024 characters; `compatibility` over 500 characters;
`metadata` that is not a string-to-string map; `allowed-tools` that is not a string.

Warnings: body over 500 lines or ~5000 tokens; missing `README.md`; an unquoted
`description` containing a colon; a `description` over 950 characters; no `Use when …`
clause; unedited template placeholders; unrecognised frontmatter keys; CRLF endings;
tabs in frontmatter; executables outside `lib/`; **any shell script**; an entry point
missing its `#!/usr/bin/env python3` shebang or executable bit; a broken `_lib`
import bootstrap; and non-portable subprocess usage.

**Language checks.** Python is the only accepted source language. The linter flags a
shell script by extension (`.sh`, `.bash`, `.zsh`, `.ksh`) *or* by shebang, anywhere in
the skill including `tests/`. For Python it verifies that every entry point — a file
with an `if __name__ == "__main__":` block — carries the shebang and the executable bit,
that any script referencing `_lib` walks up the correct number of `parents[N]` to reach
the skills root (2 from `lib/`, 1 from the skill root), and that no file that spawns
processes shells out to `sha1sum`, `stat -c/-f`, `date -d/-v`, `setsid` or `mktemp`, or
passes `shell=True`. Importable modules are exempt from the shebang and exec-bit checks.

### `lib/skill_new.py`

Scaffolds a new skill, then lints it.

```bash
lib/skill_new.py <skill-name> [--root <dir>]
```

Validates the name against the spec before touching the filesystem, creates
`<root>/<skill-name>/` with `SKILL.md`, a `README.md` stub and an empty `lib/`, then
runs the linter. Refuses to overwrite an existing directory. `--root` defaults to
`~/.agents/skills`.

You write `lib/<entry>.py` yourself: give it a `#!/usr/bin/env python3` shebang, the
executable bit, and the `_lib/skillkit` bootstrap. The linter checks all three.

Exits `2` on an invalid name or bad usage, `1` if the destination exists or the template
is missing.

The rendered description is a placeholder that fails `--strict` by design, so an
unedited skeleton cannot ship unnoticed.

### `templates/SKILL.md.template`

The skeleton `skill_new.py` renders. `SKILL_NAME` is substituted; the remaining
placeholders (`SHORT_SUMMARY`, `DESCRIBE_THE_TRIGGER`, `ONE_LINE_PURPOSE`,
`CONCRETE_GOTCHA`) are intentionally left for the author and are flagged by the linter
until replaced.

## Design

**Deterministic checks live in the script; semantic work lives in `SKILL.md`.** A linter
can prove a description is 1145 characters, but only an agent can rewrite it to 900
without losing the phrasings that trigger it. So repair is a documented procedure whose
final step is re-running the deterministic gate, not a script that pretends to
understand intent.

**Two severities, never merged.** Conflating "this will not load" with "this has no
README" trains agents to ignore both. Errors block; warnings inform.

**Linting is not proof of loading.** A well-formed file can still be rejected.
`--list-names` exists so its output can be diffed against `copilot skill list` — the
runtime loader's own answer — which is the check whose absence let three skills
disappear.

**Layout follows the team, not the spec's suggestion.** The specification presents
`scripts/`, `references/` and `assets/` as recommendations. This repository uses `lib/`
and `templates/`, so the linter enforces consistency with the sibling skills rather
than a naming convention no local skill follows.

**A shell script is a warning, not an error — deliberately.** A `.sh` file does not stop
a skill loading, and `Report.loadable` drives `--list-names`, which is diffed against
`copilot skill list` to detect drift. Promoting the language rule to `ERROR` would drop
every still-loading skill out of that list and report drift that does not exist, breaking
the check that matters most. `--strict` is the enforcing gate instead, so a new skill
cannot ship shell while a mid-port skill still lints truthfully.

**`_lib` is not a skill.** It has no `SKILL.md`, and discovery skips it by name, so the
shared package never appears as a broken skill in a sweep.

**The checks are import-aware, not mention-aware.** Naming `skillkit` in prose, or
listing `sha1sum` in a pattern table, is not a violation. The bootstrap check fires on an
actual `import skillkit`, and the portability check only inspects files that really spawn
processes, skipping `re.compile(...)` lines — otherwise this linter would flag itself.

**Frontmatter parsing uses only the Python standard library.** Python does not ship a
general YAML parser, so `skill-lint.py` intentionally accepts the subset used by Agent
Skills frontmatter: top-level keys, quoted and plain scalars, simple `metadata` maps,
simple sequences, and literal or folded block scalars. This keeps the skill portable to
system Python installs without requiring `PyYAML`.

## Dependencies

`python3`, and the shared `~/.agents/skills/_lib/skillkit` package. No network access.
