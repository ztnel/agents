# Changelog

All notable changes to this project will be documented in this file.

## [0.2.1] - Unreleased

### Added
- The `writer` skill for concise human-facing documentation without semantic loss.
- The standalone `github-issue` skill with a separate draft workspace, duplicate-search gate, metadata validation, and reviewed issue publication flow.
- Root `mcp.json` for the official GitHub MCP streamable HTTP endpoint scoped to `repos,issues`.
- CI regression and Copilot package smoke coverage for manifests, skills, tuicr shell tests, and live agent discovery.

### Changed
- Agent worktrees are staged under `~/.worktrees/<repo>/<branch>`.
- tuicr supports agent-authored `description` and `review-note` context, and unanswered comments no longer block approval.
- The `agents:developer` workflow now preserves the local tuicr review repo/session across issue-draft revisions.
- Approved tuicr close reports remain the gate for reviewed code delivery and now also authorize GitHub issue publication.
- Provenance rules now require concrete model IDs and human email identities in reviewed issue drafts.
