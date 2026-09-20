# `_toolkit` — workflow scripts for the developer agent

Not a skill. Like `_lib`, the leading underscore marks a directory the skill
loader ignores: there is no `SKILL.md`, so nothing here is ever presented to a
model as an activatable capability. These are plain entry points that an agent
runs by absolute path.

## Provenance

Most of this directory is the salvage of the former `dev` skill. That skill's
prose was removed because it had rotted past the point of being runnable — ten
of the eleven script paths its `SKILL.md` told an agent to execute did not
exist, pointing either at a `dev-lite/` directory that had been renamed or at
five `dev/lib/*.py` files belonging to a deleted predecessor. Its *scripts*,
however, were sound and unit-tested, so they were kept and the workflow prose
they belonged to now lives in the `developer` agent.

The layout is deliberately a sibling of `_lib`: every entry point bootstraps
the shared package with `parents[2] / "_lib"`, and `profile_resolve` resolves
its bundled TOML with `parents[1] / "templates"`. Both hold here with no edits.
Moving these scripts anywhere shallower would mean rewriting those paths in
every file for no benefit.

## Public contract

Each script is a standalone CLI. All of them emit `KEY=VALUE` lines on stdout
for the caller to consume, and use distinct non-zero exits so a caller can tell
*why* something failed rather than only *that* it did. Run any of them with
`--help` for the full argument list.

### `role_guard.py {snapshot,enforce}`

Enforces a sub-agent's declared file ownership and reports what it authored.
This is what makes "the adversary owns the tests, the generator owns the
implementation" real rather than aspirational — the CLI has no per-agent path
allowlist, so the boundary is checked after the fact against `git status`.

```bash
role_guard.py snapshot --repo <dir> --out <baseline>
role_guard.py enforce  --repo <dir> --baseline <baseline> --allow <glob>... [--dry-run]
```

Bracket exactly one dispatch with the two subcommands. The snapshot is what
makes it safe to run in a dirty tree: only paths whose content changed
*between* snapshot and enforce are attributed to the sub-agent, so unrelated
work already in progress is invisible to the guard.

| Emits | Meaning |
| --- | --- |
| `AUTHORED=<path>` | In-scope change. Accepted, and the author feed for the provenance ledger. |
| `REVERTED=<path>` | Out of scope, and the guard restored it. |
| `BLOCKED=<path>` | Out of scope but **already dirty** before the dispatch. Reported, never auto-reverted. |

`BLOCKED` is the important case. A path that was already carrying uncommitted
changes cannot be restored from git, so reverting the sub-agent's edit would
also destroy the human's work. The guard refuses to make that trade and hands
the conflict to a human instead.

Globs are git-style: `**` spans separators, `*` and `?` do not, and a trailing
`/` means "this directory and everything under it".

Exits: `0` all in scope, `2` usage, `3` not a repository or unreadable
baseline, `4` out-of-scope changes found.

### `profile_resolve.py [--repo D] [--profile P]`

Resolves the TOML platform profile that keeps everything job-specific out of
the workflow: branch naming, ticket ids and URLs, ticket fetch, PR templates,
PR open command, and the monthly credit cap. Search order is `<repo>/.dev.toml`,
then `~/.config/dev/profile.toml`, then `templates/profiles/default.toml`.

Also the importable helper the other scripts use — they call `load_profile()`
rather than re-reading the TOML.

`HAS_TICKET_FETCH=0` means no tracker command is configured, so the ticket text
has to come from the human. `HAS_PR_OPEN=0` means the human opens the PR.

### `branch_parse.py <branch> [--repo D]`

Validates a branch name against the profile's `[branch].pattern` and parses it
once, up front, so everything downstream keys off the same values. Emits
`BRANCH`, `PREFIX`, `TICKET_ID`, `TICKET_URL`, `SLUG`, `SCOPE`,
`WORKTREE_SUFFIX`.

A rejection quotes the profile's own `[branch].describe`, so the human sees
their convention rather than a regex. A rejected branch is a profile mismatch,
not a bug — check which profile resolved before changing anything, and never
loosen a pattern to make one branch fit.

Exits: `2` usage, `3` no profile, `4` branch does not match.

### `pr_render.py --worktree D --ticket-url U [--title T] [--description F]`

Renders `PR.md` in the worktree from the profile's PR template, falling back to
the bundled vendor-neutral `templates/pr-fallback.md` so a repo with no
template still gets a usable body. Places the ticket link by convention rather
than by title, so it works for any tracker.

Adds `PR.md` to the worktree's `.git/info/exclude` so it never shows up in a
review diff. That file lives in the **common** git dir, not the per-worktree
one; the script resolves it correctly, so do not hand-edit it.

Exits: `2` usage, `3` worktree missing, `4` `PR.md` exists (use `--force`),
`5` no template and the fallback is missing.

### `budget_check.py [--cap N] [--month YYYY-MM] [--by-model]`

Reports month-to-date AI credit spend against the cap, reading the CLI's own
session store. With `--by-model`, breaks spend down per model, busiest first —
per-turn cost varies several-fold between models, which is the single most
useful number when choosing who to dispatch.

**A report, not a gate: it always exits 0**, including when over cap, so it can
never block work mid-feature. It writes a `WARN:` line to stderr instead.
Deciding what to do about a warning belongs to the human.

Unit assumption: 1 AIC = 1 AIU = 1e9 `nano_aiu`. If a billing page disagrees,
correct it with `--cap` or the profile's `[budget].cap_aic` rather than by
editing the script — the comparison only needs both numbers in the same unit.

Exits: `2` usage, `3` store missing or has no usage table.

### `teardown.py --worktree D --branch B [--merged] [--prune-remote]`

Retires a merged feature's worktree and local branch; `--prune-remote` also
deletes the remote branch.

`--merged` is a caller-asserted gate: without it nothing is removed and the
script only reports what teardown *would* do. The removal is a force-remove
that discards any work still in the worktree, so it must never be reachable by
accident — pass it only after the human confirms the merge.

Exits: `2` usage, `3` worktree missing or helper not installed, `4` branch
undeterminable or worktree unsafe to remove.

## Related tooling

These live in real skills and are called from there, not duplicated here:
`tuicr/lib/tuicr_up.py` and `watch_up.py` for the review surface and its watch
daemon, `git-worktree/` for worktree lifecycle, and `git-commit/commit.py` for
provenance-stamped commits.

## Testing

```bash
cd .agents/skills/_toolkit
for t in tests/test_*.py; do python3 "$t"; done
```

Tests use temp dirs, temp git repos, temp SQLite fixtures and `PATH` overrides
— no network, no daemon, and nothing written outside `/tmp`.
