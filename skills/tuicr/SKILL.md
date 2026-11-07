---
name: tuicr
description: "Read comments and human-owned review marks in tuicr code-review sessions, open review windows in tmux, and run a live feedback loop through the tuicr-feedback MCP server. Includes contracts for answering every comment before acting and for interpreting the deterministic close report without setting marks. Use when opening a review for a human, reading or replying to review comments, waiting for review feedback, checking review completion, or handling a tuicr review closure event."
---

# tuicr review workflow

`tuicr` is the human's review surface. The TUI is where they read the diff and
write comments; the `tuicr review` CLI is how an agent discovers sessions, reads
comments, and posts replies. The `tuicr-feedback` MCP server closes the loop
by delivering pending comments and the review's closure as durable batches, so
the human never has to copy anything out of the TUI.

Scripts live in this skill's `lib/`. Resolve the dir as
`~/.agents/skills/tuicr/lib`. They need `tuicr`, `git`, `tmux` and `python3`
(3.8+, stdlib only; `feedback_server.py` also needs `mcp`, provided by the
plugin's `uv run --project skills/_lib`), and run on Linux, macOS and WSL —
`tmux` has no native Windows port, so on Windows use WSL. See [`lib/README.md`](lib/README.md) for
each script's contract and exit codes.

## Non-negotiables

1. **Close the loop in tuicr.** Whenever you act on a comment, post a threaded
   `--type reply` *before* you report the work done, anchored to that comment's
   **exact** `path`, `start_line`, `end_line` and `side` — threading is
   positional, so an approximate anchor does not thread. A chat summary is not a
   substitute — the human triages in tuicr. See
   [Replying](#replying-to-existing-comments).
2. **Open a review without being asked.** Whenever you produce changes a human
   needs to review, proactively open a tuicr window on them and say it is ready.
   The only exception is an explicit "no review window for this one".
3. **Refresh branch refs first.** Before opening any branch-based review, fetch
   and resolve against fresh remote refs via
   [`lib/refresh_review_refs.py`](#opening-a-review). Never review a stale local
   base. Fixed commit-SHA ranges need no refresh.
4. **The feedback loop is local-only.** While handling feedback an agent may
   read comments, make **unstaged** edits, and post **local draft** replies. It
   must never stage, commit, push, or sync to a remote forge. The human's review
   gate depends on this. Committing happens **outside** the loop, after the
   human has approved and the review is over — never in response to a feedback
   event.
5. **Orient the reviewer in tuicr, not the source.** For agent-authored changes,
   add one review-level `description` comment when opening the review. Add
   concise `review-note` comments only where reviewer-specific context is
   likely to prevent confusion. Do not put temporary review guidance into code
   comments or durable documentation.

## Pick the workflow

**A — Human-led review of your changes.** They inspect the patch and write
comments. You open or find the session and read their comments; you do not
review your own patch or write comments in their voice.

**B — Agent review of a patch** (any "code review this", "review with model X
and Y" request). Spawn a window for the target revset and post every finding as
an inline comment — anchored in tuicr, not printed to chat. Run reviewer
sub-agents in parallel when several models are named, verify each finding
against the real source before posting (models fabricate line numbers and
symbols), and set `--username` to the model id that produced it.

Ask only when the *session* is ambiguous (several plausible repos, or an active
session on a different revset). If the *workflow* is ambiguous, ask which one.

## Opening a review

Resolve the revset first. If it contains branch refs, refresh them:

```bash
eval "$(~/.agents/skills/tuicr/lib/refresh_review_refs.py \
  --repo <repo> [--base <branch>] [--head <branch-or-HEAD>])"
# use ${REVIEW_REVSET}
```

The base defaults to the remote's own default branch (`origin/HEAD`), so no
branch name is hardcoded. The helper fetches, resolves names to fresh
`origin/<branch>` refs, fast-forwards a **clean** behind `HEAD`, and refuses a
dirty or diverged one. Pass `--no-ff` to review the local `HEAD` as-is instead
of moving it. If a commit in the range is already merged into the base, use
`<first>^..<last>` so the diff is non-empty.

Then check for an existing session and open a window if there is none:

```bash
tuicr review list --repo <repo>                       # look for "active": true
~/.agents/skills/tuicr/lib/tuicr_up.py <repo>         # working-tree review window
```

`tuicr_up.py` refreshes refs, opens a tmux window, prints
`Review session: <slug>`, and blocks until tuicr exits, then writes the
[close report](#close-contract). Run it as an attached background shell
command, then `connect_review(repo, session=<slug>)` on the `tuicr-feedback`
MCP server. If tuicr is already reviewing **that same checkout**, it exits
without opening another window; connect to the active session instead. For
full control, open directly: `tmux new-window -d -n review
-c <repo> "tuicr -w"` (uncommitted changes) or `"tuicr -r '<revset>'"` (a
commit range). Requires `$TMUX`; if there is no multiplexer, tell the human
you are waiting for them to start `tuicr`, then attach via `tuicr review
list`. A session not launched by `tuicr_up.py` has no close report, so its
closure is unknown (`closed: null`).

The CLI works outside tmux, so never require a multiplexer just to read an
existing session.

For agent-authored changes, post one concise review-level description after the
session is available:

```bash
tuicr review add --repo <repo> --session <slug> \
  --type description --username "<your-model-id>" \
  "<intent, scope, and the most important review focus>"
```

Post it once per session; do not duplicate it when reusing an active session.

**Reuse the persisted session for that checkout and review target.** Review
marks are human-owned state: unchanged files retain their marks, while tuicr
automatically clears marks for changed files and adds new files unreviewed.
Never move, replace, delete, or edit a persisted session file to refresh its
file list or force a new review. Only do so when the human explicitly requests
session-state maintenance.

## Reading comments

There is no push stream from the TUI. Read on demand.

**Use `review.py`, not raw `tuicr`.** It is the validated front door for the
four operations you perform, and it removes the mistakes that are easy to
make from a shell and hard to diagnose:

```bash
~/.agents/skills/tuicr/lib/review.py --repo <repo> comments [--unanswered]
```

- `--repo` is a **subcommand** flag on the real CLI, so `tuicr review --repo
  <path>` fails with `unexpected argument '--repo' found` and a usage block that
  never names the actual problem — the missing subcommand. `review.py` requires
  the subcommand up front and says so.
- `--session` defaults to the repo's active session, and `--unanswered` applies
  the pending-comment rule (non-`reply`, no later reply at the same anchor) for
  you rather than leaving you to reproduce it.

The raw form below is the reference for what the wrapper sends, and is still
what you need for anything outside those three operations (`--type reply`, for
instance):

```bash
tuicr review comments --repo <repo> --session <slug>
```

Each comment carries `id`, `location`, `path`, `start_line`, `end_line`,
`side`, `comment_type`, `lifecycle_state`, `created_at`, `content`.

> tuicr has **no parent/thread field** — threading is positional. A reply is
> associated with its parent by sharing the **same anchor** (`path`,
> `start_line`, `end_line`, `side`) and a later `created_at`. This is why reply
> anchoring must match exactly.

Treat types as: `issue` blocking, `suggestion` consider-or-justify, `note`
answer, `praise` no action, and agent-authored `description` / `review-note`
context only. If the result is empty, confirm the session choice. Re-read
before claiming completion — the human may have kept commenting while you
worked.

## Feedback contract

The `tuicr-feedback` MCP server delivers feedback as durable batches. **This
section is the behaviour**, identical for every agent.

1. **Connect and wait.** `connect_review(repo, session)` returns a
   `review_id`. Loop on `wait_for_feedback(review_id, author=<your-model-id>)`
   (or poll `receive_feedback`). Waiting occupies a tool call; an idle agent
   receives nothing until it calls again. A batch replays — also across server restarts — until you
   acknowledge it, and reads and replies never acknowledge.
2. **Handle each event in order.**
   - `message` — a pending human comment. Skip it if `superseded`. Otherwise
     re-read the latest contents of the anchored file at the current checkout
     state before deciding how to answer, so your reasoning reflects the
     newest code even if the comment was written against an older snapshot.
   - `withdrawn` — no longer pending (not necessarily deleted). No action.
   - `closure` — follow the [Close contract](#close-contract).
3. **Classify, then reply — before any work.** Every pending comment gets a
   threaded reply via `reply(review_id, message_id, message_version, content,
   author)`, which posts a `--type reply` at the original's **exact** anchor;
   that reply is what closes it. A `version` mismatch means the comment
   changed: reread before replying. What the reply *says* depends on what the
   comment **is**. `comment_type` is only a hint — it is user configuration
   (one human's `note` is another's `question`), so read the intent.

   - **Asks for a change** — a problem, a suggestion, a nit. Reply with one
     lowercase verdict and a brief justification:
     - `agree — <why>; queued: <short concrete implementation/test brief>`
     - `disagree — <why>; no work started`
     - `needs clarification — <question>; no work started`

     A bare verdict is not enough, and an `agree` must accurately describe the
     work you are about to start.
   - **Asks a question** — the human wants to understand something. **Just
     answer it, in plain prose.** No verdict word, no template, no `queued:`.
     Answer directly, then stop: a question is not a change request and starts
     no work.
   - **Just remarks** — praise, an FYI, "this is intentional". One short
     acknowledgement. Do not manufacture a verdict for it.

   A comment can be two of these at once ("why 5.0? make it configurable"):
   answer the question plainly *and* give a verdict on the ask — never force the
   answer into verdict shape. If answering a question exposes a real defect, say
   so in the answer and queue it as you would an `agree`.
4. **Acknowledge** with `acknowledge_feedback(review_id, batch_id)` only after
   every message in the batch is replied to and any queued work is recorded.
   Then wait again.
5. **Then act, through your own ownership model.** The contract fixes *when* and
   *what you promise*, never *who edits*. If you are a solo coding agent, make
   the edits yourself. If you orchestrate sub-agents, route the brief to the
   owning agent and do not edit code yourself. If a workflow skill defines
   ownership (worktrees, test/impl partitioning, a todo ledger), that skill
   decides the routing — this contract does not override it.
6. **`disagree`, `needs clarification`, an answered question, and an
   acknowledged remark all start no work.** Wait for the human's follow-up,
   which arrives as a new comment in a later batch.
7. **Be idempotent.** A batch may replay. If a comment already has your reply
   or queued work, do not duplicate it.
8. **Report the outcome** in the same thread before calling the comment done,
   unless your first reply already states it (an answered question usually
   needs nothing further).
9. **Stop at the local boundary.** Unstaged edits and local draft replies only.
   The human reviews the diff and the threads afterwards.

Without the MCP server, read pending comments with `review.py --repo <repo>
comments --unanswered` and reply with `review.py reply --to <comment-id>` (see
[Replying](#replying-to-existing-comments)); the same rules apply.

> **No status updates in tuicr.** Proactive agent comments are limited to one
> review-level `description`, targeted `review-note` context, and explicitly
> registered decision questions to the human. Everything else
> you post is a `--type reply` anchored to a human comment. Agent context types
> and registered questions are never pending. Report progress in your own
> window, never in the review.

## Close contract

If a changed file type remains `unreviewed` because the binary retains an old
status, follow [changed-file-type recovery](lib/README.md#recovering-a-changed-file-type).
The human unmarks and saves, the gate observes that mark, then the human reviews,
re-marks, and saves the unchanged diff. Never toggle marks or manipulate
persisted session files yourself.

Closure arrives as a `closure` event whose `closure` is `{"verdict":
"<approved|incomplete|aborted>", "report": "<path>"}`; it is authoritative only
for sessions launched by `tuicr_up.py`. Read the JSON report at `report` and
act on its deterministic result. Closure alone never implies approval.

- `approved` means tuicr exited cleanly, `HEAD` did not move, the current change
  set is non-empty, every changed file is present and human-marked reviewed at
  its current content, and no registered questions to the human remain open.
  `unanswered` contains only those questions, not ordinary review comments.
- `incomplete` means the review closed cleanly but one or more checks failed.
  Report the exact `reasons` and unreviewed/absent/stale files. Also mention
  unanswered registered questions when present; those block approval.
- `aborted` means the window was killed, crashed, or otherwise did not record a
  clean exit. It is never approval.

Review marks are human-owned. Read and verify them; never create, change, or
simulate them. Preserve the persisted session so unchanged marks survive future
review cycles; let tuicr invalidate marks when content changes. Approval ends
this skill's responsibility: do not stage, commit, push, or infer what approval
enables. The invoking custom agent or human-owned workflow decides the next
action.
For capability consumers, pass the report's `approval` receipt through the
provider-neutral `skillkit.approval` interface, not the tool-specific report.

### Questions to the human

When an agent asks a decision question in tuicr, post it with the agent's model
ID and immediately register its comment ID through
`review.py --repo <repo> --session <slug> register-question --comment-id <id>`.
Do not infer question intent from punctuation or configured comment types.
`description` and `review-note` are never questions.

After the human directly answers, link the answer's exact ID using
`resolve-question --comment-id <question-id> --answer-id <answer-id>`.
The answer must be newer, non-empty, and authorless in the persisted session;
agent model-authored replies cannot resolve it. This author convention is not
cryptographic authentication. Edits or deletion invalidate resolution;
re-register edited questions and link a new human answer.
Registered questions are never pending feedback. The skill-owned
registry never changes persisted tuicr session files or human review marks.

## Writing comments

Use line comments when you know the file and line, file comments for
file-scoped feedback, and review-level only for whole-review summaries. Always
pass `--username "<your model id>"` so agent comments are distinguishable.

| `--type` | When |
|----------|------|
| `issue` | A bug, regression, or safety problem the author must address. |
| `suggestion` | A non-blocking improvement they may take or skip. |
| `note` | Context or an answer; no action required. |
| `praise` | Something done well. Use sparingly. |
| `description` | One concise review-level orientation for agent-authored changes: intent, scope, and review focus. |
| `review-note` | Reviewer-specific clarification attached to the smallest useful file or line range; no action required. |
| `reply` | A threaded response to an existing comment. **Always** use this when responding, anchored to the original's file/line(s)/side. |

> Types come from the human's tuicr config (`comment_types`), not from tuicr
> itself — `--type` defaults to `none`. If an agent type is rejected, the config
> lacks it: tell the human rather than silently posting untyped.

### Agent review context

Use `description` and `review-note` to keep reviewer-only prose out of the
source:

- `description` is exactly one review-level comment per session. State what
  changed, why, and where review attention is most valuable.
- `review-note` is optional and sparse. Attach it to the smallest useful file,
  line, or range when a section has a non-obvious tradeoff, constraint, or
  testing implication that a reviewer needs but future source readers do not.
- Keep durable rationale, public behavior, and maintenance constraints in the
  code or project documentation. Review notes are not a substitute for required
  source documentation.
- Do not restate the diff, narrate implementation steps, or add generic notes.

```bash
tuicr review add --repo <repo> --session <slug> \
  --target-file <path> --line <start> --end-line <end> --side <side> \
  --type review-note --username "<your-model-id>" \
  "<brief reviewer-specific context>"
```

### Size budget (required)

Comment text is re-rendered every frame for **every** comment in the session, so
cost accumulates across the whole review. Observed: smooth at ~10–15k total
characters, slower at ~18k, heavily laggy past ~21k.

- **~1500 characters hard cap** per comment.
- **Post fewer comments.** Never re-post a reply to reword it.
- **At most ~10 inline markup spans**, never stacked (no `` **`likeThis`** ``).
- **No fenced code blocks.**

If it does not fit, post the decisive point and give the full argument to the
human in your own window. tuicr is a review surface, not a place to publish
essays.

### Replying to existing comments

tuicr comments have **no parent or thread field**. Threading is purely
positional: a reply appears under a comment only when its anchor matches
**exactly**, on all four of

| field | must equal the original's |
| --- | --- |
| `--target-file` | `path` |
| `--line` | `start_line` |
| `--end-line` | `end_line` |
| `--side` | `side` |

Copy those four straight out of the comments JSON. Never re-derive them by
looking at the diff — the session pins them to the reviewed snapshot, and your
line numbering may not match it.

**Always pass `--end-line`.** It is optional to the CLI, not to you: omit it on
a comment spanning lines 10–14 and the reply is filed at 10–10, a *different*
anchor.

**The reliable way to get all four right is not to type them.** `review.py
reply` takes the **id** of the comment you are answering and copies that
comment's anchor verbatim, so the anchor cannot be mistyped, half-copied, or
re-derived from the wrong diff:

```bash
~/.agents/skills/tuicr/lib/review.py --repo <repo> reply \
  --to <comment-id> --username "<your-model-id>" \
  "<what you changed / your answer>"
```

It prints the anchor it used (`ANCHOR=<path>:<start>-<end>(<side>)`) so you can
confirm the thread landed where you intended, and rejects a reply over the
~1500-char budget before it reaches the session. Get the ids from
`review.py --repo <repo> comments --unanswered`.

The raw form is what that sends:

```bash
tuicr review add --repo <repo> --session <slug> \
  --target-file <path> --line <start_line> --end-line <end_line> \
  --side <side> \
  --type reply --username "<your-model-id>" \
  "<what you changed / your answer>"
```

Getting the anchor wrong fails **twice over**, and neither failure is obvious:

1. The reply lands as a separate top-level comment instead of under the human's
   — so they never see your answer next to their question.
2. Pending detection uses the *same* anchor tuple to decide a comment has been
   answered. A mis-anchored reply leaves it **still pending**, so it is
   redelivered in later feedback batches.

If the original's file or line no longer exists, still anchor at the original
`path`/`start_line`/`end_line` from the comments JSON — the session pins those
to the reviewed diff, and the human needs the answer next to their question.
Use the newest file contents only to understand the current code, not to
reconstruct the anchor yourself.

Reserve review-level replies (no `--target-file`) for genuinely review-wide
summaries. `--side old` is for removed lines, `--side new` for added or
unchanged ones. `--input` accepts literal JSON, `@file.json`, or `-` for stdin.

## Error handling

| Situation | Action |
|-----------|--------|
| Multiple plausible active sessions | Ask which slug to use |
| No active session, tmux available | Open one with `tuicr_up.py` |
| No active session, no multiplexer | Say you are waiting for the human to start `tuicr` |
| `tuicr` not installed | Tell the human to install it |
| Comments empty | Confirm the session, or ask them to save comments |
| Base/head may be stale | Run `refresh_review_refs.py`; use `REVIEW_REVSET` |
| `HEAD` behind + dirty worktree | Stop; reconcile before review |
| `HEAD` diverged from upstream | Stop; rebase/merge as directed |
| An agent comment type is rejected | The human's `comment_types` config lacks `reply`, `description`, or `review-note` |
| Your reply did not thread, or a comment you answered is redelivered | The anchor did not match exactly — re-post with the original's `end_line` and `side` |
| `tuicr-feedback` reports `closed: null` | The session was not launched by `tuicr_up.py`; closure is unknown |

## When not to use

- The human only wants raw `git diff` output.
- They explicitly asked for a non-tuicr review flow.
- Remote PR review with no tuicr session involved.
