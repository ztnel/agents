# tuicr

Review-loop Agent Skill for the `tuicr` code review surface.

## Public API

The skill exposes Python entry points in `lib/` for running a review in the current terminal, reading and replying to comments, refreshing branch references, writing the deterministic close report, and serving the `tuicr-feedback` MCP server that delivers comments and closure to agents. `SKILL.md` defines the operational contract, including agent-authored `description` and `review-note` context that stays in the review instead of the source.

## Design criteria

The workflow keeps human review local and explicit: agents may make unstaged edits and draft replies while handling feedback, but staging, committing, pushing, and remote synchronization stay outside the loop until the human approves.
