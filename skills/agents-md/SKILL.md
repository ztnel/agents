---
name: agents-md
description: "Audit-and-merge editor for AGENTS.md files. AUTO-INVOKE this whenever a human asks to edit, modify, update, add, change, or remove a rule in an AGENTS.md file. It audits the ENTIRE target file, finds existing rules on the same topic, and MERGES the new rule into them instead of duplicating it. Contradictions, conflicts of interest, or any change that weakens a safety-critical rule are a HARD STOP for human decision. Never auto-commits: present the merged result as an unstaged edit plus a before/after diff. Use when changing AGENTS.md; reading AGENTS.md for context does not invoke it."
---

# `agents-md` — audit-and-merge editor for `AGENTS.md`

Owns the **discipline** of changing an `AGENTS.md` file: never a blind append.
Every edit begins with a full audit of the existing file, folds the incoming
rule into whatever is already there, and stops on any contradiction so a human
decides. `AGENTS.md` is the agent's working contract and is **human-owned
context** — this skill exists so that contract stays coherent instead of
accreting duplicate, drifting, or contradictory rules.

The helper lives in this skill's `lib/`; resolve it as
`~/.agents/skills/agents-md/lib/agents_md.py`. It is **read-only** with respect
to the `AGENTS.md` (locate / outline / snapshot / diff); the actual edit is made
with your normal edit tool so it stays in the reviewable path.

## When this auto-invokes

Any request to **edit / add / update / change / remove a rule in an `AGENTS.md`**
(e.g. "add a rule to AGENTS.md that…", "update the testing section of AGENTS.md",
"put this convention in AGENTS.md"). Reading an `AGENTS.md` for context does
**not** invoke it — only a request to change one does.

## Mandatory policy (read before touching any `AGENTS.md`)

These rules are non-negotiable.

1. **The human's edit request is the permission — nothing else is.** `AGENTS.md`
   is human-owned context; you may change it **only** because a human explicitly
   asked. Never edit an `AGENTS.md` on your own initiative or as a side effect of
   another task. **If a conflict arises and the human is unavailable to resolve
   it, STOP and wait** — do not guess.
2. **Audit the ENTIRE file first — no blind append.** Before proposing anything,
   read the whole target file and run `agents_md.py outline` to enumerate every
   section and its rules. You must understand what already exists before adding
   to it. Appending to the bottom without auditing is a policy violation.
3. **Merge, don't duplicate.** Fold each incoming rule into the existing rules on
   the same topic (see the decision table). Only add a fresh entry when nothing
   related exists, and put it in the section where it topically belongs.
4. **A conflict of interest is a HARD STOP.** If an incoming rule contradicts,
   weakens, or changes the meaning/default of an existing rule, do **not** edit.
   Surface the conflict to the human (format below) and wait for their decision.
5. **Safety-critical rules cannot be weakened silently.** Any incoming rule that
   would relax, override, or carve an exception into a safety-critical rule
   (hardware actuation, human ownership of context, human review before commit,
   approval gates, no-force-push) is **always** treated as a hard conflict —
   surface it and require the human to explicitly confirm the override, even if
   the request seems to imply it.
6. **Never auto-commit; present unstaged + your own diff.** Make the change as an
   unstaged edit, then show the human a `agents_md.py diff` (snapshot vs. current)
   plus a short change summary. `AGENTS.md` is usually **gitignored**, so
   `git diff`/tuicr will not show it — the snapshot diff is the review artifact.
   Committing (if the human asks) is a separate, human-gated step.
7. **Surgical & style-preserving.** Touch only the rules the request is about.
   Match the file's existing heading depth, numbering vs. bullets, tone, and
   wording conventions. Do not reformat, reorder, or "improve" untouched sections.
8. **Never create a new `AGENTS.md` unless explicitly told to.** If no `AGENTS.md`
   exists at the target, do not create one on your own — confirm with the human
   that they want a new file created.
9. **Beware symlinked / shared files.** `agents_md.py locate` flags when the
   target is a symlink to the main worktree. Editing it changes the **shared**
   canonical file for every worktree — tell the human before proceeding.

## Procedure

```bash
LIB=~/.agents/skills/agents-md/lib/agents_md.py
```

1. **Locate the target.** If the human named a path, pass it; otherwise enumerate
   candidates and pick the nearest (confirm with the human if ambiguous or if
   more than one plausible `AGENTS.md` exists):
   ```bash
   "$LIB" locate --repo "$(pwd)"           # or: --path <explicit AGENTS.md>
   ```
   Note any symlink / gitignore warnings it prints.
2. **Audit the whole file.** Read it in full (use `view`; for a file over the
   read limit, read it in ranges so nothing is skipped) and map its structure:
   ```bash
   "$LIB" outline --file <AGENTS.md>
   ```
   You now have every section and every existing rule in context.
3. **Classify each incoming rule** against the audited rules using the decision
   table below.
4. **If any rule conflicts → STOP and surface it** (format below). Do not write
   the conflicting rule until the human decides. Non-conflicting rules may still
   proceed, but hold a conflicting one back rather than partially applying.
5. **Snapshot, then edit.** Capture the pre-edit state, then make the merges/adds
   with your edit tool (in-place, in the right sections):
   ```bash
   SNAP=$("$LIB" snapshot --file <AGENTS.md> | sed 's/^SNAPSHOT=//')
   ```
6. **Present for review (never commit).** Show the diff and a summary:
   ```bash
   "$LIB" diff --file <AGENTS.md> --snapshot "$SNAP"
   ```
   Summarize as: **merged-into** (which existing rule), **added** (which section),
   **skipped-as-duplicate** (already covered by …), **conflicts-surfaced**. The
   human reviews the unstaged change; committing is their separate call.

## Merge decision table

For each incoming rule, compare it against the rules found in step 2:

| Situation | Action |
|-----------|--------|
| **Exact duplicate / already a subset** of an existing rule | **Skip.** Report "already covered by §<section>". Add nothing. |
| **Same topic, compatible** (adds nuance, scope, or an exception) | **Merge** — edit the existing rule's wording to absorb it. Do **not** add a second bullet. |
| **New topic, compatible** | **Add** to the most appropriate existing section (new section only if none fits). |
| **Contradicts / weakens / changes the default of** an existing rule | **HARD STOP — surface to human.** Do not edit that rule. |
| **Weakens a safety-critical rule** | **HARD STOP — surface + require explicit human override**, always. |

## Surfacing a conflict (blocking)

When step 4 finds a conflict, present it exactly like this, then **ask the human
and wait** — do not proceed on that rule:

```
⚠️  RULE CONFLICT — human decision required
New rule:       "<incoming rule>"
Conflicts with: §<section>, line <n>: "<existing rule, quoted>"
Nature:         <contradiction | weakening of a stronger rule | safety-critical override | changed default>
Options:
  1) Keep the existing rule, drop the new one
  2) Replace the existing rule with the new one
  3) Reconcile into one merged rule:  "<proposed merged wording>"
  4) Keep both, scoped so they don't overlap  (only if genuinely non-overlapping)
```

Use the `ask_user` tool for the decision. For a safety-critical override, require
the human to state the override explicitly — an implied "yes" is not enough.

## Scripts (`lib/`)

- `agents_md.py locate` — resolve the target `AGENTS.md` (or enumerate
  candidates + nearest), flagging symlink-to-main-worktree and gitignored status.
- `agents_md.py outline` — print a section + rule-count outline so the whole file
  is audited before editing.
- `agents_md.py snapshot` — copy the current file (deref symlink) before editing.
- `agents_md.py diff` — unified before/after diff for human review (works even
  when the file is gitignored). All four are **read-only** on the `AGENTS.md`.

See `README.md` for the full script contract.

## Constraints

- **`AGENTS.md` is human-owned context.** The only reason to edit it is an
  explicit human request; that request is the permission. No self-initiated edits.
- **Conflicts and safety-critical overrides are hard human gates.** Never resolve
  them yourself; surface and wait. If the human is unavailable, STOP.
- **Never auto-commit or auto-push.** Present the change as unstaged plus the
  snapshot diff; committing is a separate human-gated step (see `git-commit`).
- **Never weaken a safety-critical rule silently**, and never trigger hardware
  actuation. This skill only edits text and never actuates anything.
