# `github-issue`

Standalone GitHub issue drafting and publication for Copilot CLI.

## Public API

- `SKILL.md` defines the repository-resolution, duplicate-search, review, and
  MCP publication contract.
- `lib/issue_draft.py` renders and validates the markdown draft format.
- `lib/issue_workspace.py` stores drafts and metadata in a dedicated git-backed
  state workspace under `XDG_STATE_HOME/agents/github-issue`, with one baseline
  commit per draft repo and later saves left unstaged for tuicr.
- `lib/issue_review.py` authorizes publication from a reviewed tuicr close
  report, guards duplicate search, and prepares a validated issue payload that
  keeps the final provenance table in the published body.

## Design criteria

Keep the skill standalone, Python-only, and stdlib-first. Repository-specific
research, adaptive evidence, metadata validation, explicit publication
failures, and strict identity checks belong here; generic prose tightening
belongs to `writer`.
