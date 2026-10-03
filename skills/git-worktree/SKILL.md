---
name: git-worktree
description: "Create, list, and remove git worktrees for running multiple agents in parallel on the same repository without conflicts. Stages worktrees under ~/.worktrees/<repo>/<branch>, and wraps fetch, submodule init, branch cleanup, and relinking gitignored local agent context back to the main worktree. Use when the user asks to create, inspect, or clean up a git worktree for parallel branch or agent work."
allowed-tools: bash
---

# git-worktree

Use this skill when the user wants to work on multiple branches/features of
the same repository in parallel — typically because multiple agents (or
multiple terminal sessions) need isolated checkouts.

This skill only handles **worktree lifecycle mechanics**. It does NOT:
- Decide which module/feature each parallel agent should own (that is a
  human coordination decision — keep it out of the skill).
- Launch other agent sessions. The user opens a new shell / tmux pane in
  the new worktree path themselves.
- Merge, rebase, or open PRs. Use `git` / `gh` for that.

## When to use

- User asks to "start a new feature branch in a worktree".
- User wants several agents working in parallel on the same repo.
- User asks to clean up a worktree after a PR is merged.

## When NOT to use

- Single-branch work in the existing checkout — just `git switch`.
- Cross-repo work — use separate clones.

## Scripts

All scripts live next to this file and accept `-h` for usage.

### Create a worktree

```
.agents/skills/git-worktree/worktree_new.py [--copy-agent-context] <branch> [base]
```

- `branch` — new branch name (created if it does not exist).
- `base`  — base ref (default: `origin/main`, falls back to `origin/master`).
- `--copy-agent-context` — copy the local agent context instead of symlinking
  it (see step 5). Use only when you deliberately want branch-specific context.

Behaviour:
1. Runs `git fetch origin` (so `base` is current).
2. Creates the worktree at `~/.worktrees/<repo-name>/<branch>`. Branch `/`
   separators remain directory separators, so `feat/auth` is staged at
   `~/.worktrees/<repo-name>/feat/auth`.
3. If `<branch>` already exists locally or on `origin`, checks it out
   instead of creating a new one.
4. If `.gitmodules` is present, runs `git submodule update --init --recursive`
   inside the new worktree.
5. **Symlinks** the main worktree's local-only agent context into the new
   worktree so it has a single source of truth: the whole `.agents/` directory
   (skills) becomes one symlink, and every `AGENTS.md` file becomes an
   individual **relative** symlink back to the main worktree (the first entry of
   `git worktree list`). Both are gitignored in this repo, so they never appear
   via checkout; `AGENTS.md` links are only created where the destination
   directory already exists on the checked-out branch. Pass
   `--copy-agent-context` to get independent copies instead (the old behavior).
6. Prints the absolute path of the new worktree and a hint to `cd` there
   and open a new agent session.

### Migrate existing worktrees to symlinks

```
.agents/skills/git-worktree/relink_agent_context.py [--apply] [--all | <branch>]
```

Converts worktrees that still hold **copied** agent context (e.g. created
before symlinking was the default) into symlinks pointing at the main worktree.

- Default is a **dry-run** — it only reports what would change. Pass `--apply`
  to perform the conversion.
- `--all` (default) processes every worktree except the main one; pass a
  `<branch>` to process just that worktree.
- A copy whose content **differs** from the canonical file is backed up to
  `<path>.pre-symlink.bak` before being replaced, so local drift is never
  silently destroyed. Identical copies and already-correct symlinks are
  converted/skipped cleanly (idempotent).
- Refuses to touch the main worktree (it holds the canonical files).

### List worktrees

```
.agents/skills/git-worktree/worktree_list.py
```

Wraps `git worktree list` with the branch each worktree is on. Use this
before creating or removing to see current state.

### Remove a worktree

```
.agents/skills/git-worktree/worktree_remove.py <branch> [--force] [--delete-branch]
```

- `branch` — the branch whose worktree should be removed. The script
  finds the matching worktree path from `git worktree list --porcelain`.
- `--force` — pass through to `git worktree remove --force` (allows
  removing a worktree with uncommitted changes).
- `--delete-branch` — also run `git branch -d <branch>` afterwards
  (use `-D` semantics by combining with `--force`).

Behaviour:
1. Refuses to remove the main worktree.
2. Without `--force`, refuses if the worktree has uncommitted changes.
3. Runs `git worktree remove`.
4. Runs `git worktree prune`.
5. Optionally deletes the local branch.

The script does NOT delete build directories or other untracked artifacts
inside the worktree path beyond what `git worktree remove` handles. If you
created a sibling `build-<branch>/` directory, remove it manually.

## Typical session

```
# Agent A starts feat/foo
.agents/skills/git-worktree/worktree_new.py feat/foo
# -> created /home/cs/.worktrees/repo/feat/foo
# (open new shell there, start a Copilot session)

# Agent B starts feat/bar in parallel
.agents/skills/git-worktree/worktree_new.py feat/bar

# Later, after feat/foo PR is merged:
.agents/skills/git-worktree/worktree_remove.py feat/foo --delete-branch
```

## Notes

- **Agent context is shared by symlink.** `.agents/` and every `AGENTS.md` in a
  worktree are symlinks to the main worktree, so editing any of them edits the
  **canonical** file and the change is visible from every worktree — that is the
  intended single-source behavior. Create the worktree with
  `--copy-agent-context` (or restore one file from its `*.pre-symlink.bak`) if a
  branch genuinely needs its own divergent context.
- Worktrees share one `.git` directory, so `git fetch` in any worktree
  updates remotes for all of them.
- Each worktree needs its own build directory if the build system writes
  absolute paths (e.g., CMake). Convention: `build-<branch>/` inside the
  worktree, or a sibling `build-<repo>-<branch>/`.
- Project-specific setup (Python venv, container toolchains, code
  generators) is **out of scope** for this skill — run the relevant
  project skill inside the new worktree after `cd`-ing into it.

## Troubleshooting

- **"fatal: '<branch>' is already checked out at ..."** — the branch is
  already in another worktree. Use `worktree_list.py` to find it.
- **"fatal: invalid reference: origin/main"** — the repo's default branch
  is not `main`. Pass an explicit `base` argument
  (e.g., `worktree_new.py feat/x origin/develop`).
- **Submodule auth errors** — handle the same way as the project's
  submodule setup (SSH key, credentials). The skill does not paper over
  these.
- **Dangling `.agents` / `AGENTS.md` symlinks** — the agent-context symlinks are
  **relative**. If the main worktree or a staged worktree is moved (or you
  removed and recreated the main worktree), the links break. Re-point them by
  re-running
  `relink_agent_context.py --apply --all` from any worktree.
- **Removing a worktree never deletes shared context** — `worktree_remove.py`
  (via `git worktree remove`) deletes the symlinks in that worktree, not their
  targets, and refuses to remove the main worktree, so the canonical
  `.agents/`/`AGENTS.md` files are always preserved.
