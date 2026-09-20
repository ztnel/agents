# `agents-md` helper API

`agents_md.py` is a read-only helper for human-owned context:

| Subcommand | Contract | Exit codes |
|---|---|---|
| `locate [--repo dir] [--path file]` | Lists candidates, nearest context, symlink/canonical facts, and Git status. | `2` bad use, `3` missing path/repo. |
| `outline --file file` | Prints headings, per-section rule counts, and totals. | `2` bad use, `3` missing file. |
| `snapshot --file file [--out file]` | Copies dereferenced content to a review snapshot and emits `SNAPSHOT=…`. | `2` bad use, `3` missing source. |
| `diff --file file --snapshot file` | Prints unified before/after content diff; differences still exit `0`. | `2` bad use, `3` missing input. |

## Design

The helper never writes `AGENTS.md`, stages, commits, or pushes. Default
snapshots use per-user state rather than `/tmp`, so a review artifact survives
until the human has examined the corresponding unstaged edit.
