---
name: git-commit
description: Commit code to git with a standardized provenance/approval trailer block — a single comma-separated Co-authored-by line with one entry per distinct author model (a single commit can mix authors, e.g. a generator wrote the impl and the adversary wrote the tests), Approved-by the local human's git identity, and a single Reviewed-by line listing the human approver first followed by any agent reviewer model IDs. Commits only what the human has already staged (never stages anything itself; if nothing is staged, nothing is committed); never pushes. Use when a human asks to commit reviewed changes and wants authorship/review provenance recorded in the commit message.
allowed-tools: bash
---

# `git-commit` — provenance-stamped git commits

Owns the **mechanics** of producing a commit whose message records *who and what*
produced and approved the change:

- the **author model** (the agent that generated the code — may differ from the
  agent running this skill),
- the **human approver** (local git identity), and
- the **reviewers** (the human approver, plus any number of agent reviewers).

The script lives next to this file. Resolve it as
`~/.agents/skills/git-commit/commit.py`.

## Commit message format

```
<one-sentence description>

Co-authored-by: Copilot (<author-model-1>) <223556219+Copilot@users.noreply.github.com>, Copilot (<author-model-2>) <...>
Approved-by: <name> <email>
Reviewed-by: <name> <email>, Copilot (<reviewer-1>) <...>, Copilot (<reviewer-2>) <...>
```

- **Description** — the commit subject. Keep it brief, ideally a single sentence.
- **Co-authored-by** — a **single comma-separated line** using the team's existing
  Copilot identity, with the **author model ID embedded in the name**. These are
  the models that *generated* part of the staged code, not necessarily the one
  running this skill. **One entry per distinct author model** (mirroring the
  `Reviewed-by` format) — a single commit may mix authors (e.g. the developer's
  generator wrote the implementation and the adversary wrote the tests).
- **Approved-by** — the local human's git identity (`user.name` / `user.email`).
- **Reviewed-by** — a **single comma-separated line** with the **human approver
  first** (proving they explicitly reviewed *and* approved), followed by zero or
  more agent reviewers in `Copilot (<model>)` form. Always present, so review is
  always recorded.

## Roles & model IDs

- **Author model(s)** — the model(s) that wrote the staged code. Pass one
  `--author-model` per distinct author (**repeatable**): if you generated it, your
  own model ID; if sub-agents wrote it, *their* model IDs (e.g. the generator
  for impl files + the adversary for test files). The developer resolves these
  from its per-file provenance ledger for the staged set. Defaults to
  `$COPILOT_MODEL` when none is given.
- **Reviewer models** — any agent(s) that reviewed the change. Pass one
  `--reviewer-model` per agent reviewer. The human approver is added
  automatically as the first reviewer; agent reviewers are optional.

## Quick start

```bash
# After the human has staged exactly what they want committed and approved it:
git add <paths>      # the human (or you, at their direction) stages the changes
~/.agents/skills/git-commit/commit.py \
  --description "Add CAN bus timeout guard to the inverter driver" \
  --author-model "<model-that-wrote-the-code>" \
  --reviewer-model "<agent-reviewer-model>" \
  --confirm-reviewed
```

With no agent reviewer (human review only) — still records review:

```bash
~/.agents/skills/git-commit/commit.py \
  --description "Fix off-by-one in telemetry ring buffer" \
  --author-model "claude-opus-4.8" \
  --confirm-reviewed
```

## Constraints

- **Human-review gate, never bypassed.** The script refuses to commit without
  `--confirm-reviewed`. Set that flag **only after** a human has explicitly
  confirmed they reviewed the staged changes. When asked to commit, first ask
  the human whether they have reviewed the changes; if the human is unavailable,
  **stop and wait** — do not commit.
- **Never auto-push.** This skill only commits. Pushing is a separate,
  human-instructed step. The script never pushes and never force-pushes.
- **Never stages anything.** The human stages exactly what they want committed
  (`git add`). The script commits only what is already staged; if nothing is
  staged, nothing is committed.

## Script

See `README.md` for the full `commit.py` argument contract and behaviour.
