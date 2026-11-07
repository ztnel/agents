# Changelog

All notable changes to this project will be documented in this file.

## [0.5.0] - Unreleased

### Changed
- Skill authoring no longer requires or scaffolds duplicate READMEs; `SKILL.md` is the capability document. Removed the presentation README.
- Split `demystify` into the standalone `presentation` skill and the `agents:demystifier` custom agent. Invoke `skills/presentation/lib/presentation.py`; the old skill and entry point are removed.
- Presentation retains rendering, anchored comments, live updates, PDF export and saved conversations. Invoking agents own content and workflow policy; page closure is not approval.
- The developer agent presents a brief outcome, requirements, constraints, non-goals, approach and acceptance criteria before implementation, requiring explicit agreement on the current brief.
- Material design changes trigger reconsideration; parallel work stays within one design-heavy feature, and code review highlights behavior, complexity, tradeoffs and acceptance evidence.

## [0.4.1] - 2026-11-02

### Added
- Numbered claim/diagram citations, a final Sources section and `gd` source jumps.
- `Ctrl-o`/`Ctrl-i` source-jump history restores cursor, diagram part and viewport.
- `K` inspects cited reference details in a floating window without navigating away.
- Comment `u` undo for deletions, paste and Insert-mode sessions.
- `demystify` inner text objects: `viw` selects a word without adjacent spaces; `vip` selects a paragraph.
- Comment Normal mode: Esc leaves Insert mode without closing the editor; Vim motions, visual selections and clipboard yanks work inside it, and `i` resumes editing.
- Comment editing: `x` deletes, `a` inserts after the cursor, `A` inserts at line end, and `p` pastes the clipboard once. Inline keybinding hints are removed.
- Local Highlight.js syntax highlighting for code fences in explainers and comment threads, preserving source-line anchors.

### Fixed
- Live thread refreshes no longer detach an open comment editor while a message is being sent.

## [0.4.0] - Unreleased

### Added
- `demystify` contents sidebar (`gO`, `:toc`) highlighting the current section; `{`/`}` jump between sections.
- Comments render bold, italic, code, lists, fenced code and LaTeX math.

### Changed
- On a diagram, `h`/`l` cycle its labelled parts instead of entering its text.
- `V` selects whole screen rows; `h`/`l` continue across a paragraph's sentences.

## [0.3.1] - Unreleased

### Added
- `demystify` interactive widgets: ```` ```widget ```` blocks run agent HTML and JavaScript in a sandboxed frame with `dm.plot` and `dm.state` helpers; comments include the widget's current values. `:e` reloads the page in place.

### Changed
- Display math is a single cursor stop like a diagram; commented equations show a side bar, and the cursor blinks.

## [0.3.0] - Unreleased

### Added
- `demystify` skill: agents explain concepts on a live, read-only page of Markdown and inline SVG diagrams, the human comments with Vim keys, agents wait and reply with their model id, and the page saves as a PDF. Comments anchor to lines, diagram parts or character selections (`v`); commented text stays highlighted and opens its thread on click. The Vim cursor and `/` search reach every character, including diagram text. LaTeX math (`$…$`, `$$…$$`, ```` ```math ````) renders as native MathML without dependencies.

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
