# `agents-md` scripts

## `lib/agents_md.py`

A **read-only** helper for the `agents-md` skill. It never writes an `AGENTS.md`
— the skill's audit-and-merge edit is made with the agent's normal edit tool so
the change stays in the reviewable path. This script only **locates**,
**outlines**, **snapshots**, and **diffs** the file, because `AGENTS.md` is
human-owned context that is frequently **gitignored** and/or a **symlink** to the
main worktree, and a self-contained snapshot+diff is the only reliable way for a
human to review such a change.

### Usage

```
agents_md.py locate   [--repo <dir>] [--path <file>]
agents_md.py outline  --file <file>
agents_md.py snapshot --file <file> [--out <path>]
agents_md.py diff     --file <file> --snapshot <path>
```

### Subcommands

| Subcommand | Purpose |
|------------|---------|
| `locate` | With `--path`, validate and report facts about one file. Without it, enumerate every `AGENTS.md` under the repo root (from `--repo`, default cwd) and print the `NEAREST=` one walking up from that directory. Reports symlink target + canonical path (flagging that editing a symlink changes the **shared** file for every worktree) and git tracked/ignored status. |
| `outline` | Print a heading-by-heading outline of the file with a rule-item count per section and totals, so the whole file is audited before any edit. |
| `snapshot` | Copy the current file content (dereferencing a symlink) to `--out` (default: a unique file in per-user state) and print `SNAPSHOT=<path>`. Run this **before** editing. |
| `diff` | Unified before/after diff between the snapshot and the current file. Works regardless of gitignore. Prints `# (no changes)` if identical. |

### Output keys

- `locate` emits `TARGET=`/`CANDIDATE=`/`NEAREST=` lines plus indented facts.
- `snapshot` emits `SNAPSHOT=<path>` (capture it: `SNAP=$(... | sed 's/^SNAPSHOT=//')`).

### Behaviour & exit

- Validation failures exit non-zero with an `agents_md.py: error:` message
  (missing `--file`, path not found, unknown
  subcommand, etc.).
- `diff` exits `0` whether or not the files differ (a difference is the expected
  case, not an error).
- The script **never** modifies, stages, commits, or pushes anything.

### Example

```bash
LIB=~/.agents/skills/agents-md/lib/agents_md.py

"$LIB" locate  --repo "$(pwd)"                 # find + flag the target
"$LIB" outline --file ./AGENTS.md              # audit the whole file
SNAP=$("$LIB" snapshot --file ./AGENTS.md | sed 's/^SNAPSHOT=//')
# ... agent makes the audited merges/adds with its edit tool ...
"$LIB" diff --file ./AGENTS.md --snapshot "$SNAP"   # review artifact
```
