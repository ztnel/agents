---
name: tuicr
description: "Read comments and human-owned review marks in tuicr code-review sessions, open review windows in tmux, and run a live feedback loop. Includes a daemon that wakes an agent when comments are pending or a review closes, plus contracts for answering every comment before acting and interpreting deterministic approval without setting marks. Use when opening a review for a human, reading or replying to review comments, checking review completion, starting or stopping a live watch, or when woken by a `tuicr wake` or `tuicr close` prompt."
---

# tuicr review workflow

`tuicr` is the human's review surface. The TUI is where they read the diff and
write comments; the `tuicr review` CLI is how an agent discovers sessions, reads
comments, and posts replies. A watch daemon closes the loop by waking the agent
when a comment is pending, so the human never has to copy anything out of the
TUI.

Scripts live in this skill's `lib/`. Resolve the dir as
`~/.agents/skills/tuicr/lib`. They need `tuicr`, `git`, `tmux` and `python3`
(3.8+, stdlib only), and run on Linux, macOS and WSL — `tmux` has no native
Windows port, so on Windows use WSL. See [`lib/README.md`](lib/README.md) for
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
4. **The watch loop is local-only.** A woken agent may read comments, make
   **unstaged** edits, and post **local draft** replies. It must never stage,
   commit, push, or sync to a remote forge. The human's review gate depends on
   this. Committing happens **outside** the loop, after the human has approved
   and the review is over — never in response to a wake.

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

`tuicr_up.py` refreshes refs, opens a tmux window, and starts the review
watch daemon for the current Copilot CLI session; if tuicr is already
reviewing **that same checkout**, it reuses the active session and still starts
the watcher. For full control, open directly: `tmux new-window -d -n review
-c <repo> "tuicr -w"` (uncommitted changes) or `"tuicr -r '<revset>'"` (a
commit range). Requires `$TMUX`; if there is no multiplexer, tell the human
you are waiting for them to start `tuicr`, then attach via `tuicr review
list`.

The CLI works outside tmux, so never require a multiplexer just to read an
existing session.

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
answer, `praise` no action. If the result is empty, confirm the session choice.
Re-read before claiming completion — the human may have kept commenting while
you worked.

## Wake contract

When a `tuicr wake <token>: N pending comment(s) in review session <slug> …`
prompt arrives, the daemon has confirmed that comments are awaiting a response.
The prompt is intentionally minimal — **this section is the behaviour**, so it
is identical no matter which agent is woken.

Before replying to anything, refresh your understanding of the file at the
current checkout state. Re-read the latest file contents for the anchored path
before deciding how to answer, so your reasoning reflects the newest version of
the code even if the comment was written against an older snapshot. Never
derive reply anchors from the current file text; use the comment id to copy the
original anchor verbatim.

`N` is the **total** outstanding count, not just what changed since the last
wake, so it may exceed the number the human wrote most recently — earlier
comments still awaiting a reply are included. Answer all of them.

1. **Read** `~/.agents/skills/tuicr/lib/review.py --repo <repo> comments
   --unanswered`. That flag applies the pending rule for you: the non-`reply`
   comments with no later reply at the same anchor. Note each comment's `id` —
   it is what you reply to.
2. **Classify, then reply — before any work.** Every pending comment gets a
   threaded reply at the original's **exact** anchor, which
   `review.py reply --to <comment-id>` copies for you (see
   [Replying](#replying-to-existing-comments)); that reply is what closes it,
   and a comment left unanswered — including one whose reply missed the anchor —
   is re-delivered. What the reply *says* depends on what the
   comment **is**. `comment_type` is only a hint — it is user configuration (one
   human's `note` is another's `question`), so read the intent.

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
3. **Then act, through your own ownership model.** The contract fixes *when* and
   *what you promise*, never *who edits*. If you are a solo coding agent, make
   the edits yourself. If you orchestrate sub-agents, route the brief to the
   owning agent and do not edit code yourself. If a workflow skill defines
   ownership (worktrees, test/impl partitioning, a todo ledger), that skill
   decides the routing — this contract does not override it.
4. **`disagree`, `needs clarification`, an answered question, and an
   acknowledged remark all start no work.** Wait for the human's follow-up,
   which arrives as a new comment and a new wake.
5. **Be idempotent.** A wake may be redelivered. If a comment already has your
   reply or queued work, do not duplicate it.
6. **Report the outcome** in the same thread before calling the comment done,
   unless your first reply already states it (an answered question usually
   needs nothing further).
7. **Stop at the local boundary.** Unstaged edits and local draft replies only.
   The human reviews the diff and the threads afterwards.

> **No status updates in tuicr.** The only comments you post are `--type reply`
> responses anchored to a human comment. A non-`reply` comment adds review
> noise *and* wakes you again through the watcher — a self-wake
> loop. Report progress in your own window, never in the review.

## Close contract

When a `tuicr close <token>: ... verdict=<approved|incomplete|aborted> ...
report=<path>` prompt arrives, read the JSON report at `report` and act on its
deterministic result.

- `approved` means tuicr exited cleanly, `HEAD` did not move, the current change
  set is non-empty, every changed file is present and human-marked reviewed at
  its current content, and every comment has an exact-anchor reply.
- `incomplete` means the review closed cleanly but one or more checks failed.
  Report the exact `reasons`, unreviewed/absent/stale files, and unanswered
  comments.
- `aborted` means the window was killed, crashed, or otherwise did not record a
  clean exit. It is never approval.

Review marks are human-owned. Read and verify them; never create, change, or
simulate them. Approval ends this skill's responsibility: do not stage, commit,
push, or infer what approval enables. The invoking custom agent or human-owned
workflow decides the next action.

## Live watch

Starts a non-LLM daemon that polls the session's persisted comments and wakes
your CLI session when one is pending. Full detail: [`reference/watch.md`](reference/watch.md).

```bash
~/.agents/skills/tuicr/lib/watch_up.py \
  --repo <repo> --session <slug> \
  --cli-session "<your full session id from your session context>" \
  --name "<label>"
```

It runs detached (no tmux window), is idempotent per `--name`, and prints
`WATCH_PID` / `WATCH_LOG`. The pane is resolved from your session id — do not
pass `$TMUX_PANE`, which is wrong whenever the caller was launched via
`tmux new-window`. Stop with `--stop <name>`, sweep with
`--stop-all [--prefix <p>] [--exclude <s>]`, inspect with `--list`.

Retire a watcher whenever its review surface stops matching what it watches
(for example after a commit moves the reviewed `HEAD`).

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
| `reply` | A threaded response to an existing comment. **Always** use this when responding, anchored to the original's file/line(s)/side. |

> Types come from the human's tuicr config (`comment_types`), not from tuicr
> itself — `--type` defaults to `none`. If `--type reply` is rejected, the
> config lacks that type: tell the human rather than silently posting untyped.

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
2. The watch daemon uses the *same* anchor tuple to decide a comment has been
   answered. A mis-anchored reply leaves it **still pending**, so you get woken
   for it again, and again, until `--max-attempts` is spent.

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
| `--type reply` rejected | The human's `comment_types` config lacks `reply` |
| Your reply did not thread, or you are re-woken for a comment you answered | The anchor did not match exactly — re-post with the original's `end_line` and `side` |
| Watcher will not start | See [`reference/watch.md`](reference/watch.md) troubleshooting |

## When not to use

- The human only wants raw `git diff` output.
- They explicitly asked for a non-tuicr review flow.
- Remote PR review with no tuicr session involved.
