---
name: skill-writer
description: "Author, validate and repair Agent Skills so they actually load. AUTO-INVOKE whenever creating, editing or debugging a skill or SKILL.md file, when porting skill scripts to Python, or when a skill that exists on disk never appears in an agent's context. Malformed skills fail silently: an over-long description, a name that does not match its directory, a BOM or unparseable YAML frontmatter each make the runtime skip the skill with no error in any log. Scaffolds new skills from a compliant template, lints frontmatter against the agentskills.io specification — load-blocking errors kept separate from conventions — and drives description-compression repairs that preserve trigger coverage. Enforces Python as the only source language and the shared _lib/skillkit package, flagging leftover shell scripts, missing shebangs or exec bits and non-portable shell-outs. Use when writing or fixing a SKILL.md; for AGENTS.md use agents-md."
allowed-tools: bash
---

# `skill-writer` — skills that actually load

Agents author and maintain their own skills. A malformed skill **fails silently**: the
runtime skips it, nothing is written to `~/.copilot/logs/`, and the authoring agent has
no way to notice. This skill is the feedback loop that absence creates.

Owns `SKILL.md` (agent-owned capability). The `agents-md` skill owns `AGENTS.md`
(human-owned context). No overlap.

Scripts live next to this file:

- `~/.agents/skills/skill-writer/lib/skill-lint.py` — the validator (read-only)
- `~/.agents/skills/skill-writer/lib/skill_new.py` — the scaffolder

## Python only

**Python is the sole source language for skill scripts.** No `bash`, `sh` or `zsh` —
not in `lib/`, not in `tests/`, not at the skill root. Every portability bug these
skills have hit was an environment bug, not a logic bug: GNU vs BSD flag differences,
and bash 3.2 on macOS lacking `declare -A` and `mapfile`. Python has neither class of
problem, and it is the reason the team supports Linux, macOS and WSL from one tree.

The linter reports a shell script as a **warning**, not an error — a `.sh` file does not
stop a skill loading, and `--list-names` must keep reporting it (see *Prove it loads*).
`--strict` is the gate that actually blocks it, so new skills cannot ship shell.

### Shared code lives in `_lib/skillkit`

`~/.agents/skills/_lib/` is a **shared package, not a skill** — it has no `SKILL.md` and
the linter skips it. It holds the primitives every skill needs (`proc`, `cli`, `paths`,
`tmuxio`, `tuicrio`, `copilot`, `gitio`, `lock`, `errors`), so a portability fix lands
once instead of once per skill. Read `_lib/README.md` before writing a new entry point.

Bootstrap it by walking up to the skills root — the depth depends on where the script
sits, and the linter checks that the depth is right:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))  # <skill>/lib/x.py
```

Never re-solve what `skillkit` already solves. Shelling out to `sha1sum`, `stat -c`,
`date -d`, `setsid` or `mktemp`, or passing `shell=True`, reintroduces exactly the
breakage the port removed; the linter flags all of them.

Entry points — any script with an `if __name__ == "__main__":` block — need
`#!/usr/bin/env python3` and the executable bit, or the invocation documented in
`SKILL.md` fails. Importable modules need neither and are not flagged.

## The rule that matters most

**`description` has a hard limit of 1024 characters.** One character over and the skill
is skipped with no diagnostic anywhere. This is the single most common way a
hand-written skill disappears, and it is invisible without linting.

Three of this team's own skills were lost to it before this skill existed.

## Severity contract

Two classes, never conflated. Mixing them trains agents to ignore both.

| Severity | Meaning | Effect |
|---|---|---|
| `ERROR` | Spec violation | The skill does not load, or breaks the published contract |
| `warn` | Convention or spec recommendation | The skill loads; hygiene issue |

`skill-lint.py` exits `1` on any ERROR, `0` otherwise. `--strict` promotes warnings to
failures — use it for new skills, where there is no legacy to excuse.

## Workflow: create a skill

```bash
~/.agents/skills/skill-writer/lib/skill_new.py <skill-name>
```

Creates a compliant skeleton — `SKILL.md`, a `README.md` stub and an empty `lib/` — then
lints it. The rendered description is a placeholder that fails `--strict` by design; an
unedited skeleton must never ship.

1. Scaffold → verify: the command reports `Created …` and lints with 0 errors
2. Write the description (see below) → verify: lint reports no description finding
3. Write the body, under 500 lines → verify: no body-size warning
4. Write `lib/<entry>.py` in Python, with a `#!/usr/bin/env python3` shebang, the
   executable bit and the `_lib/skillkit` bootstrap → verify: no shell-script, shebang,
   exec-bit or bootstrap warning
5. Fill `README.md` → verify: no README warning
6. Gate: `skill-lint.py <dir> --strict` exits 0
7. Prove it loads (see *Prove it loads*)

## Workflow: validate

```bash
# every skill in ~/.agents/skills
~/.agents/skills/skill-writer/lib/skill-lint.py

# one skill, held to the full standard
~/.agents/skills/skill-writer/lib/skill-lint.py ~/.agents/skills/<name> --strict

# machine-readable
~/.agents/skills/skill-writer/lib/skill-lint.py --json
```

Run the full sweep after editing *any* skill. A single sweep is cheap and catches the
skill you did not realise you broke.

## Workflow: repair a skill that will not load

Compressing a description is semantic work — no script can do it, because only an agent
knows which trigger phrasings must survive. The procedure:

1. `skill-lint.py <dir>` → read the exact violation and the current character count.
2. If the description is over 1024, **relocate, do not delete.** Move the displaced
   detail into the SKILL.md body, where the full text loads on activation anyway. The
   description exists only to answer *should I load this?* — implementation detail in it
   is wasted budget.
3. Preserve trigger coverage. Keep every distinct *situation* that should activate the
   skill; drop mechanism, file names, flag names and internal architecture.
4. Aim for **≤ 950 characters**, not 1023. The linter warns above 950 because a skill
   sitting one character under the limit is one small edit from vanishing.
5. Re-lint → verify: 0 errors.
6. Prove it loads.

Leave repairs as **unstaged edits for human review**. Never commit them.

## Writing a description that triggers

The description carries the entire burden of activation — it is all an agent sees before
deciding whether to load the skill.

- **Say when, not just what.** Agents match on intent. End with an explicit
  `Use when …` clause. The linter warns if one is missing.
- **Be pushy.** Name the situations that should activate it, including ones where the
  human will not name the domain. `AUTO-INVOKE whenever …` is appropriate for skills
  that must not be missed.
- **Name the boundary.** If an adjacent skill could be confused with this one, say so
  outright (`for AGENTS.md files use the agents-md skill instead`). This is the cheapest
  fix for cross-triggering.
- **Quote it.** Always wrap the value in double quotes. An unquoted description
  containing `:` parses only until a colon happens to be followed by a space.
- **Budget it.** Under 950 characters.

## Prove it loads

**Linting is necessary but not sufficient.** It proves the file is well-formed, not that
the runtime accepted it. Close the loop by diffing what *should* load against what the
runtime *actually* loaded — `copilot skill list` is the loader's own answer:

```bash
cd ~   # project skills are discovered relative to the working directory
diff <(~/.agents/skills/skill-writer/lib/skill-lint.py --list-names | sort) \
     <(copilot skill list | awk '/^Project skills:/{f=1;next} /^[A-Z].*skills:/{f=0} f && /^  [a-z0-9-]+ - /{print $1}' | sort)
```

Empty diff means no drift. A name only on the left is a skill that lints clean but the
runtime still rejected — keep debugging. A name only on the right is a skill from another
source (`~/.copilot/skills/`, a plugin, or `copilot skill add`).

`copilot skill list` re-reads from disk, so a repair shows up immediately. The **agent's
own context** is fixed at session start, so a repaired skill will not appear to a running
agent until a **new session**. Report that to the human rather than assuming failure.

## Gotchas

Concrete failure modes observed in this repository, not general advice.

- **Nothing is logged.** `~/.copilot/logs/` contains no record of a skipped skill. Absence
  of an error is not evidence a skill loaded.
- **A skill can be missing for months.** `jira` — a dependency of a feature-workflow
  skill and of `secretary` — sat at 1145 characters and silently never loaded.
- **`name` must equal the directory name.** Renaming a folder without editing the
  frontmatter, or vice versa, silently unloads the skill.
- **A UTF-8 BOM hides the opening `---`.** Frontmatter detection fails and the entire
  file is treated as body. Save UTF-8 without BOM.
- **Duplicate YAML keys do not error.** PyYAML keeps the last one, so a stray second
  `description:` silently overrides a good one. The linter rejects duplicates.
- **`metadata` values must be strings.** `version: 1.0` parses as a float and violates
  the spec; write `version: "1.0"`.
- **`allowed-tools` is a space-separated string, not a list.** A YAML list is invalid.
- **Em dashes and smart quotes are fine.** They are valid UTF-8 and count as one
  character each; they are not the cause of a load failure.
- **Body size does not block loading**, but the whole body enters context on every
  activation. Over 500 lines, move detail into reference files and tell the agent
  *when* to read them.
- **A `lib/*.py` without the executable bit is not an entry point.** It parses, it lints
  clean as a module, and the invocation documented in `SKILL.md` fails with "Permission
  denied". The linter checks the bit only on files with a `__main__` block.
- **A wrong `parents[N]` fails only at runtime.** `<skill>/lib/x.py` needs `parents[2]`;
  a script at the skill root needs `parents[1]`. Off by one and the import raises
  `ModuleNotFoundError: No module named 'skillkit'` when the skill is invoked, not when
  it is written.

## Team conventions

Warnings, not errors. The spec's `scripts/`/`references/`/`assets/` layout is explicitly
a recommendation; this team uses:

```
_lib/skillkit/      # shared package: portable primitives (not a skill, has no SKILL.md)
<skill>/
├── SKILL.md        # required
├── README.md       # Every source folder documents its public API
├── lib/            # executable Python entry points and modules — no shell
└── templates/      # output templates
```

Python is the only source language (see *Python only*). Shared behaviour belongs in
`_lib/skillkit`, never copied between skills.

## Safety

- The linter is strictly read-only and makes no network calls.
- Repairs are the only mutating path, and always land as unstaged edits for human review.
- Never edit an `AGENTS.md` from this skill — delegate to `agents-md`.
