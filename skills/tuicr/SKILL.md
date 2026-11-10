---
name: tuicr
description: "Read comments and human-owned review marks in tuicr code-review sessions, run a review in any terminal, and run a live feedback loop through the tuicr-feedback MCP server. Includes contracts for answering every comment before acting and for interpreting the deterministic close report without setting marks. Use when opening a review for a human, reading or replying to review comments, waiting for review feedback, checking review completion, or handling a tuicr review closure event."
---

# tuicr review workflow

`tuicr` is the human's review surface. The TUI is where they read the diff and
write comments; the `tuicr review` CLI is how an agent discovers sessions, reads
comments, and posts replies. The `tuicr-feedback` MCP server closes the loop
by delivering pending comments and the review's closure as durable batches, so
the human never has to copy anything out of the TUI.

Scripts live in `$SKILL_DIR/lib`. `$SKILL_DIR` is the directory containing this SKILL.md. Run scripts by that
absolute path, directly; they have shebangs, so never prefix `python`.
They need `tuicr`, `git` and `python3` (3.8+,
stdlib only; `feedback_server.py` also needs `mcp`, provided by the plugin's
`uv run --project skills/_lib`), and run on Linux, macOS and WSL. This skill
does not open or manage terminals; the invoking workflow decides where the
review runs. See [`lib/README.md`](lib/README.md) for
each script's contract and exit codes.

## Non-negotiables

1. **Close the loop in tuicr.** Answer every comment with the feedback
   server's `reply` and a verdict *before* any work; work waits for the human
   to close the review. A chat summary is not a substitute — the human triages
   in tuicr. See the [Feedback contract](#feedback-contract).
2. **Open a review without being asked.** Whenever you produce changes a human
   needs to review, proactively start a tuicr review on them and say it is ready.
   The only exception is an explicit "no review for this one".
3. **Never review a stale base.** `tuicr_up.py` refreshes branch refs itself.
   Only a revset you build yourself needs
   [`refresh_review_refs.py`](#building-a-revset-yourself). Fixed commit-SHA
   ranges need no refresh.
4. **The feedback loop is local-only.** An agent may read comments, post
   **local draft** replies, and, once the review closes, make **unstaged**
   edits. It
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
and Y" request). Start a review for the target revset and post every finding as
an inline comment — anchored in tuicr, not printed to chat. Run reviewer
sub-agents in parallel when several models are named, verify each finding
against the real source before posting (models fabricate line numbers and
symbols), and set `--username` to the model id that produced it.

Ask only when the *session* is ambiguous (several plausible repos, or an active
session on a different revset). If the *workflow* is ambiguous, ask which one.

## Opening a review

Check for an existing session and start one if there is none:

```bash
tuicr review list --repo <repo>                       # look for "active": true
$SKILL_DIR/lib/tuicr_up.py <repo>
```

A dirty worktree is reviewed as-is. Otherwise the launcher fetches, reviews
fresh `origin/<default>...HEAD` and fast-forwards a clean, behind `HEAD`. Set
`TUICR_BASE_REF`, `TUICR_HEAD_REF` or `TUICR_REMOTE` to change that.

### Building a revset yourself

For an agent review of a branch range (workflow B), refresh refs first:

```bash
eval "$($SKILL_DIR/lib/refresh_review_refs.py \
  --repo <repo> [--base <branch>] [--head <branch-or-HEAD>])"
# use ${REVIEW_REVSET}
```

The base defaults to the remote's own default branch (`origin/HEAD`), so no
branch name is hardcoded. The helper fetches, resolves names to fresh
`origin/<branch>` refs, fast-forwards a **clean** behind `HEAD`, and refuses a
dirty or diverged one. Pass `--no-ff` to review the local `HEAD` as-is instead
of moving it. If a commit in the range is already merged into the base, use
`<first>^..<last>` so the diff is non-empty.

### Running the launcher

`tuicr_up.py` runs tuicr **in the terminal it is started in** and blocks until
the human quits. On exit it writes the
[close report](#close-contract). Run it somewhere the human can see, never in
your own tool shell: the invoking workflow opens a terminal for it, or asks the
human to run it in one of theirs. Once tuicr is up, call `connect_review(repo)`
on the `tuicr-feedback` MCP server; comments made before you connect arrive in
your first batch. If the launcher is already reviewing **that same checkout**, it
exits 0; connect to the active session instead. A session not launched by `tuicr_up.py` has no close report, so its
closure is unknown (`closed: null`).

The `tuicr review` CLI works in any terminal, so never require a particular
terminal just to read an existing session.

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
$SKILL_DIR/lib/review.py --repo <repo> comments [--unanswered]
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

1. **Connect and wait.** `connect_review(repo)` returns a
   `review_id`. Loop on `wait_for_feedback(review_id, author=<your-model-id>)`
   (or poll `receive_feedback`). Waiting occupies a tool call; an idle agent
   receives nothing until it calls again. A batch replays — also across server restarts — until you
   acknowledge it, and reads and replies never acknowledge.
2. **Handle each event in order.**
   - `message` — a pending human comment. Skip it if `superseded`. Otherwise
     re-read the latest contents of the anchored file at the current checkout
     state before deciding how to answer, so your reasoning reflects the
     newest code even if the comment was written against an older snapshot.
   - `withdrawn` — the human removed or resolved it; your replies never cause
     one. No action: acknowledge and keep waiting.
   - `closure` — follow the [Close contract](#close-contract). A `superseded`
     closure belongs to an earlier round that has reopened: ignore it.
3. **Reply with a verdict — before any work.** Every pending comment gets a
   threaded reply via `reply(review_id, message_id, message_version, verdict,
   content, author, task)`, posted at the original's **exact** anchor; that
   reply is what closes it. A `version` mismatch means the comment changed:
   reread before replying. `comment_type` is only a hint — it is user
   configuration (one human's `note` is another's `question`), so read the
   intent and pick one verdict:
   - `accepted` — you will make the change. `task` is a one-line brief of the
     work; `content` says why. The task is **queued**, not started.
   - `declined` — you will not make it; `content` says why. Any requested
     change you keep as is is declined, never answered.
   - `answered` — a question or remark that requests no change; `content`
     answers it plainly. No work.

   A comment that is both a question and an ask ("why 5.0? make it
   configurable") is `accepted`: answer the question in `content`. If
   answering exposes a real defect, accept it and queue the fix.
4. **Queue, don't work.** Mirror every accepted task in your todo list. Edit
   nothing while the review is open: the human batches feedback by deciding
   when to close. `wait_for_feedback` and `reply` refuse once the work tree
   changes; undo the edit and keep it queued. If the human changed files,
   call `connect_review` again to accept the new state.
5. **Acknowledge** with `acknowledge_feedback(review_id, batch_id)` once every
   message in the batch has its reply. Then wait again.
6. **Apply the queue after closure.** The `closure` event carries `queued`,
   the accepted tasks of this round. With a non-empty queue the verdict is
   `changes_queued`, never `approved`. Apply the queue through your own
   ownership model — the contract fixes *when* and *what you promise*, never
   *who edits*; a workflow skill that defines ownership decides the routing.
   Then open a new review round with `tuicr_up.py`: it archives the applied
   queue, and the human's marks on unchanged files survive.
7. **Be idempotent.** A batch may replay. If a comment already has your
   reply, do not duplicate it.
8. **Stop at the local boundary.** Unstaged edits and local draft replies only.

`reply` is the only reply channel: it copies the anchor from the message,
verifies delivery, and records the verdict. Do not post replies with
`review.py` or `tuicr review add`.

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
"<approved|changes_queued|incomplete|aborted>", "report": "<path>", "queued":
[...]}`; it is authoritative only
for sessions launched by `tuicr_up.py`. Read the JSON report at `report` and
act on its deterministic result. Closure alone never implies approval.

On close, `tuicr_up.py` deletes settled comments: replies, the comments they
answer, and answered registered questions. Their anchors go stale once the work
lands, and the code should carry what was agreed. Descriptions, review notes,
unanswered comments and unanswered questions remain. Take queued work from
`queued`, not from the comments.

- `approved` means tuicr exited cleanly, `HEAD` did not move, the current change
  set is non-empty, every changed file is present and human-marked reviewed at
  its current content, and no registered questions to the human remain open.
  `unanswered` contains only those questions, not ordinary review comments.
- `incomplete` means the review closed cleanly but one or more checks failed.
  Report the exact `reasons` and unreviewed/absent/stale files. Also mention
  unanswered registered questions when present; those block approval.
- `changes_queued` means the review closed cleanly with accepted work queued.
  Apply `queued`, then open a new round; it is never approval.
- `aborted` means tuicr was killed, crashed, or otherwise did not record a
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

tuicr comments have **no parent or thread field**. Threading is positional: a
reply appears under a comment only when its `path`, `start_line`, `end_line`
and `side` all match exactly, and pending detection uses the same tuple. A
mis-anchored reply lands as a separate top-level comment *and* leaves the
original pending. The feedback server's `reply` copies the anchor from the
message itself, even when the file or line no longer exists, so never build a
reply by hand.

## Error handling

| Situation | Action |
|-----------|--------|
| Multiple plausible active sessions | Ask which slug to use |
| No active session | Start `tuicr_up.py` where the human can see it, or give them the exact command |
| `tuicr` missing or older than 0.28 | Tell the human to install or upgrade it; older versions hide unsaved reviews |
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
