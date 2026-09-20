# `git-worktree` script API

The root-level shell files are compatibility wrappers around Python entry
points. They preserve existing command names and never commit or push.

| Command | Contract | Exit notes |
|---|---|---|
| `worktree_new.py [--copy-agent-context] <branch> [base]` | Fetches `origin`, creates a sibling worktree, initializes submodules, then links (or explicitly copies) local agent context. | `2` invalid use, `3` target exists, `4` no default base. |
| `worktree_list.py` | Lists Git worktrees for the current repository. | Propagates Git failures. |
| `worktree_remove.py <branch> [--force] [--delete-branch]` | Refuses the main worktree, removes only the selected worktree, and optionally deletes its branch. | `2` invalid use, `3` no matching worktree, `4` main-worktree refusal. |
| `relink_agent_context.py [--apply] [--all \| <branch>]` | Dry-runs by default; `--apply` converts copies to relative links and backs up divergent context first. | `2` invalid use, `3` missing worktree/main root. |

## Design

`.agents/` and `AGENTS.md` are local context rather than branch content. The
default relative-link behavior gives every sibling worktree one canonical source
without putting context into Git. Migration never silently destroys divergent
copies: it saves `*.pre-symlink.bak` before replacement.
