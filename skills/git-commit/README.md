# `git-commit` scripts

## `commit.py`

Creates a commit whose message carries a standardized provenance/approval
trailer block from the changes the human has **already staged**. Never stages
anything itself (no `git add`), never pushes, never force-pushes. If nothing is
staged, nothing is committed.

### Usage

```
commit.py --description "<one sentence>" --confirm-reviewed [options]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--description "<text>"` | yes | Commit subject. Keep it brief — ideally one sentence. |
| `--confirm-reviewed` | yes | Asserts a human has reviewed the staged changes. Set **only** after explicit human confirmation. The commit is refused without it. |
| `--author-model <id>` | yes¹ | Model ID of an agent that *generated* part of the staged code. **Repeatable** — one entry per distinct model on the single comma-separated `Co-authored-by` line (a commit may mix authors). Defaults to `$COPILOT_MODEL` if set. May differ from the agent running the skill. |
| `--reviewer-model <id>` | no | Model ID of an agent reviewer. **Repeatable** — each adds an entry to the comma-separated `Reviewed-by` line, after the human approver. |
| `--approved-by "<name> <email>"` | no | Override the approver identity. Defaults to local `git config user.name` / `user.email`. |
| `-h`, `--help` | — | Print usage. |

¹ Required unless `$COPILOT_MODEL` is exported.

### Behaviour

1. Verifies the current directory is inside a git work tree.
2. Refuses unless `--confirm-reviewed` is present (human-review gate).
3. Requires a non-empty `--description` and an author model (arg or
   `$COPILOT_MODEL`).
4. Resolves the approver from `--approved-by`, else from git config; errors if
   neither yields a name and email.
5. Verifies something is already staged (the human's `git add`); never stages
   anything itself.
6. Aborts cleanly if nothing is staged.
7. Builds the message and commits. Prints the short hash and branch.
8. Never pushes.

### Output trailer block

```
<description>

Co-authored-by: Copilot (<author-model-1>) <223556219+Copilot@users.noreply.github.com>, Copilot (<author-model-2>) <...>
Approved-by: <name> <email>
Reviewed-by: <name> <email>, Copilot (<reviewer-1>) <...>, Copilot (<reviewer-2>) <...>
```

The `Co-authored-by` and `Reviewed-by` lines are each a **single comma-separated
line**: `Co-authored-by` lists one entry per distinct author model; `Reviewed-by`
always lists the human approver first, then agent reviewers (zero or more, from
repeated `--reviewer-model`).

### Examples

Human review only (no agent reviewer):

```bash
commit.py \
  --description "Fix off-by-one in telemetry ring buffer" \
  --author-model "claude-opus-4.8" \
  --confirm-reviewed
```

Author plus two agent reviewers:

```bash
commit.py \
  --description "Add CAN bus timeout guard to the inverter driver" \
  --author-model "gpt-5.3-codex" \
  --reviewer-model "claude-opus-4.8" \
  --reviewer-model "gpt-5.5" \
  --confirm-reviewed
```

### Exit behaviour

Exits non-zero with a `commit.py: error:` message on any validation failure (not
a git work tree, missing `--confirm-reviewed`, missing description/author model,
unresolved approver, or nothing staged). Staging is the human's responsibility —
the script never runs `git add`.

### Implementation

`commit.py` is a thin wrapper that execs `commit.py`; the logic is Python
(3.8+, stdlib only) sharing [`_lib/skillkit`](../_lib/README.md) with the other
skills. The wrapper name is the stable entry point, so every existing call site
is unaffected.

The rewrite was a portability fix, not a refactor: the author-model dedupe used
a bash 4 associative array, which does not exist in the bash 3.2 that macOS
ships as `/bin/bash` — so on a Mac this script failed at the point where it
assembles the trailer. It now runs on Linux, macOS and WSL. The error prefix,
exit codes, argument surface and trailer format are unchanged and covered by
`tests/test-portability.sh`.
