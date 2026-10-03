# Changelog

All notable changes to this project will be documented in this file.

## [0.2.5] - Unreleased

### Fixed
- Changed file types no longer permanently block tuicr approval. An observed human unmark/re-mark can approve the unchanged current diff even when the binary retains an old status.

## [0.2.4] - Unreleased

### Fixed
- GitHub issue publication consumes generic tuicr approval and verifies the reviewed draft and metadata against content fingerprints.
- Only explicitly registered, unanswered questions to the human block tuicr approval; orientation comments never block it.

## [0.2.3] - Unreleased

### Fixed
- New tuicr review windows inherit the caller's `EDITOR` and `VISUAL`, preserving the preferred editor without changing tmux's global environment.

## [0.2.2] - Unreleased

### Fixed
- tuicr review windows preserve terminal access for external editors such as Neovim; exports use the clipboard instead of stdout capture.

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
