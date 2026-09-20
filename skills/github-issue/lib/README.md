# `github-issue/lib`

Python helpers backing the `github-issue` skill.

## Public API

### `issue_draft.py`

Renders and parses the reviewed draft markdown format:

- H1 title
- adaptive free-form body
- final provenance table with `Identified by`, `Authored by`, and `Approved by`

`Identified by` accepts either a valid human email or a concrete model ID.
`Authored by` is concrete model IDs only. `Approved by` is a valid human email
only. Blank values, placeholders, malformed email attempts, and malformed
human-looking pseudo-model IDs are rejected. `strip_provenance()` remains
available for callers that want title+body only.

### `issue_workspace.py`

Creates and reuses dedicated git workspaces under
`XDG_STATE_HOME/agents/github-issue/<draft-id>`, creates exactly one baseline
commit so `HEAD` exists for review, stores `draft.md` and `metadata.json` as
working-tree changes, and keeps the target checkout untouched.

### `issue_review.py`

Authorizes publication from an approved tuicr close report, refuses stale or
incomplete review state, rejects open duplicates, validates issue metadata, and
assembles the final payload for `issue_write`. `AuthorizationResult` always
binds the normalized reviewed repo, but it binds reviewed draft content only
when `authorize_publication()` receives explicit `draft_markdown`. It never
recovers markdown from caller state. `prepare_issue_payload()` refuses repo or
markdown substitution, rejects authorization that lacks an explicit reviewed
draft digest, and publishes the validated body with its final provenance table.
