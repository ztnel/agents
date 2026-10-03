---
name: github-issue
description: "Draft and publish reviewed GitHub issues with repository-aware gates. Resolve an explicitly named repo or the current checkout's remote, and ask when the target is ambiguous. Inspect repository issue templates and contribution guidance before drafting, run a mandatory duplicate search, gather adaptive evidence sections, validate requested or clearly implied metadata, persist markdown plus metadata in a separate git-backed workspace, and use the official GitHub MCP search_issues, issue_read, and issue_write tools with explicit auth failures or tool errors. Keep prose concise without duplicating the writer skill. Never invent identity. Use when preparing, reviewing, or filing a GitHub issue."
---

# `github-issue` — reviewed GitHub issue drafting and publication

This skill owns the repeatable mechanics of GitHub issue reporting. It is
standalone: it does not depend on another skill's files or implementation.
Reuse `writer` only for concise prose, not for issue workflow policy.

## Contract

1. **Resolve the target repository first.** Prefer an explicit repo when the
   human names one. Otherwise resolve the current checkout's remote. If several
   remotes or repositories are plausible, ask rather than guess.
2. **Inspect repository guidance before drafting.** Read issue templates,
   `CONTRIBUTING`, support guidance, and other repository-specific issue
   instructions before deciding the draft shape or metadata.
3. **Duplicate search is mandatory.** Search open issues immediately before the
   first draft and again immediately before `issue_write`. Refuse publication if
   an open duplicate matches.
4. **Draft adaptively.** Use an H1 title, then the shortest body that still fits
   the evidence. Sections are adaptive, not fixed: include only what the report
   needs, but cover observed behavior, expected behavior, reproduction,
   environment, screenshots/logs, and proposed next action when the evidence
   makes them material.
5. **Validate metadata.** Validate labels, assignees, milestones, issue type,
   templates, projects, and other requested or clearly implied metadata before
   using them. Refuse metadata that is absent, malformed, or unsupported by the
   target repository.
6. **Keep provenance explicit.** Drafts end with a provenance table recording
   `Identified by`, `Authored by`, and `Approved by`. `Identified by` accepts a
   valid human email or a concrete model ID; `Authored by` is concrete model
   IDs only; `Approved by` is a valid human email only. Reject blanks,
   placeholders such as `copilot-auto`, `auto`, and `unknown`, and malformed
   human-looking identifiers. Never invent identity.
7. **Use the official GitHub MCP issue tools.** Use `search_issues` for
   duplicate checks, `issue_read` for inspecting candidate duplicates and
   repository issue context, and `issue_write` only after review approval. Use
   repository MCP reads for templates and contribution guidance.
8. **Publish the reviewed provenance table.** `prepare_issue_payload()`
   validates the reviewed draft, keeps the H1 title separate for `issue_write`,
   and publishes the issue body with the full final provenance table still at
   the bottom. `strip_provenance()` remains a separate utility for callers that
   want title+body only.
9. **Fail explicitly.** Authentication failures, missing tools, unsupported
   metadata, or MCP/tool errors are reported explicitly. Never pretend an issue
   was filed when publication failed.

## Workspace

Persistent drafts live under `XDG_STATE_HOME/agents/github-issue` (default
`~/.local/state/agents/github-issue`) in a dedicated git workspace. The target
repository stays clean; draft edits, metadata, and review artifacts belong to
that workspace, not to the checkout being reported on. Each draft workspace is
initialized once with exactly one baseline commit so `HEAD` exists for review.
Later `save_draft` calls leave `draft.md` and `metadata.json` as unstaged
working-tree changes in that same repo; they are never auto-committed.

Expose `draft.md` and `metadata.json` on the review surface and wait for its
approval gate. Publication consumes a provider-neutral `agents.approval/v1`
receipt through `skillkit.approval`, requiring both files' content fingerprints
and unchanged workspace `HEAD`. Read the target repository and metadata from
the reviewed `metadata.json`. The publication gate applies duplicate and
payload checks independently; it does not depend on a specific review tool.

## Public API

See [`lib/README.md`](lib/README.md) for the Python helpers that back the draft
format, persistent workspace, and publication gate.
