# `tuicr/lib` — review-session tooling

Scripts backing the `tuicr` skill: opening a review window, keeping its refs
honest, and running the live-watch daemon that wakes an agent when the human
leaves a comment.

Scripts are executable Python entry points using only the standard library.
Shared primitives live in [`../../_lib/skillkit`](../../_lib/README.md).

## Public contract

### `review.py [--repo D] [--session S] {list,comments,reply,reviewed}`

The validated front door for the `tuicr review` operations agents perform.
Agents invoke tuicr free-hand from a shell, where two mistakes are easy to make
and hard to read from the resulting error:

- `--repo` is a **subcommand** flag, so `tuicr review --repo <path>` fails with
  `unexpected argument '--repo' found` and a usage block that never names the
  missing subcommand. Here the subcommand is required by the parser, and
  `--repo`/`--session` are accepted on **either side** of it — flag position
  cannot be got wrong because it does not matter.
- A reply threads **positionally**, so it only lands under the comment it
  answers when file, start line, end line and side all match. `reply --to
  <comment-id>` copies the parent's anchor verbatim instead of asking the
  caller to reproduce it, and echoes `ANCHOR=` so the result is verifiable.

`--session` defaults to the repo's active session. `comments --unanswered`
applies the pending rule (non-`reply`, no later reply at the same anchor).
`reply` rejects a body over 1500 chars — tuicr re-renders every comment each
frame, so total comment text drives the human's TUI latency.

`reviewed [--staged] [--unreviewed] [--json] [--gate]` compares the current
Git change set with the session's persisted human review marks. Each path is
classified as `reviewed`, `unreviewed`, `absent`, or `stale`; `--gate` exits 5
unless the non-empty change set is fully reviewed. It never writes marks.

Exits non-zero with a message naming the valid subcommands when one is omitted,
and lists the available comment ids when `--to` names an unknown one.

### `tuicr_up.py [directory]`

Launches tuicr in a new tmux window over a freshly-resolved revset, starts the
watch daemon for the current Copilot CLI session, focuses the review window,
and blocks until tuicr exits. It then stops the comment watcher, evaluates
review marks against the unchanged `HEAD`, records unanswered comments as
non-blocking report context, persists a JSON verdict under the tuicr XDG state
directory, and wakes the originating agent with the report path. A killed or
failed window is always `aborted`.
The launcher leaves tuicr attached to the pane's terminal, so `e` can open a
terminal editor such as Neovim. Exported instructions use tuicr's clipboard
path rather than `--stdout` capture.

Configured by environment, not flags: `TUICR_WINDOW_NAME` (default `tuicr`),
`TUICR_BASE_REF` (default: **the remote's own default branch**, never a
hardcoded name), `TUICR_HEAD_REF` (default `HEAD`), `TUICR_REMOTE` (default
`origin`).

If tuicr is already reviewing that checkout, the launcher reuses the active
review session and still starts the watcher instead of opening another window.
Persisted sessions are reused across review cycles so unchanged human review
marks survive. tuicr owns mark invalidation when file content changes; callers
must not move, replace, delete, or edit session files to force a refresh.

Refuses (exit 1) when tuicr is missing, the target is not a git repo, or it is
not running inside tmux. Exits 0 without acting if a tuicr pane is already open
on that same checkout — scoped per-checkout so two repositories can be reviewed
at once.

### `refresh_review_refs.py [--repo D] [--base R] [--head R] [--remote R] [--no-ff]`

Fetches, resolves branch names to fresh remote-tracking refs, and fast-forwards
a clean `HEAD` that is behind its upstream.

Emits `REVIEW_BASE_REF`, `REVIEW_HEAD_REF`, `REVIEW_REVSET` as shell-`eval`-safe
`KEY=VALUE` lines.

| exit | meaning |
| --- | --- |
| 2 | unknown argument |
| 3 | repo missing, or not a git repository |
| 4 | remote does not exist |
| 5 | a ref does not resolve after the fetch |
| 6 | `HEAD` has diverged from its upstream |
| 7 | `HEAD` is behind but the worktree is dirty |
| 8 | the remote's default branch could not be determined |

Exits 6 and 7 are deliberate refusals: reviewing a stale `HEAD` shows the human
a diff that does not match the branch.

### `watch_up.py`

Daemon lifecycle for `tuicr-watch`. Generic — it knows nothing about any
workflow, so an orchestrator, a solo agent or a human drive it identically.

```
watch_up.py --repo D --session SLUG --cli-session UUID
            [--cli-pane P] [--name ID] [--persistent]
            [--ignore-type T]... [--rearm S] [--max-attempts N]
            [--event-timeout S] [--queue-timeout S] [--state-dir D]
watch_up.py --stop NAME | --stop-all [--prefix P] [--exclude S] | --list
```

Emits `WATCH_PID`, `WATCH_PIDFILE`, `WATCH_LOG`, `WATCH_NAME`,
`WATCH_PERSISTENT`. Starting is **idempotent by replacement**: if the pidfile
already names a live watcher, it is stopped and replaced with a fresh daemon
for the same `(repo, session)`. Exit 2 for a missing required option, 3 for a
missing watcher/repo, 4 if the daemon died during startup.

`--name` lets a caller running several watchers retire exactly the one it means.
`--persistent` only *tags* the watcher so a bulk `--stop-all` can skip it.

### `tuicr_watch.py`

The daemon. Watches one session and wakes a Copilot CLI session when a comment
awaits a response. Run `--help` for the full option list; `--once --replay
--dry-run` tests detection without touching tmux or state.

## Design criteria

**The wake prompt is deliberately minimal.** It names the session and the
pending count and points at the skill's Wake contract — nothing else. Behaviour
lives in the skill, not the prompt, so it is identical whether the woken session
is a lone coding agent or an orchestrator brokering to sub-agents. The human's
comment text is never echoed back into the agent's input.

**Acceptance is proven from persisted events, never from the screen.** A wake
counts as delivered only once it appears in the session's `events.jsonl` as a
`user.message`. Terminal rendering is a diagnostic and can lie.

**Pane binding is re-checked before every injection.** If the agent exited and a
shell reclaimed the pane, pasting a prompt and pressing Enter would execute it
as a shell command. This is a safety check, not an optimisation, and an
ambiguous match is always an error — never a guess. The daemon still wakes a
live CLI even if the input box already contains text.

**A tuicr failure is never "no comments".** Conflating them would leave the
daemon reporting healthy while silently ignoring the human forever.

**But a permanent failure ends the watch.** Refusing to treat a read failure as
"no comments" must not mean waiting forever: a watcher whose review window had
been closed was observed logging 920 consecutive failures with no end. The two
cases are distinguished by `review list` — if it still answers but no longer
names the slug, tuicr is up and the session is genuinely gone, so the daemon
exits 0 with a reason. If `review list` fails too, tuicr itself is down, the
session's fate is unknown, and the daemon keeps waiting. The check only runs
after a ~60 s grace period and only while already failing, so restarting tuicr
never looks like the review ending and the poll loop is unaffected.

**Threading is positional.** tuicr comments carry no parent id; a reply attaches
to a comment only when `(path, start_line, end_line, side)` match exactly. The
daemon uses that same tuple to decide "answered", so a mis-anchored reply causes
the comment to stay pending and be re-delivered until its attempt budget is
spent. `skillkit.tuicrio.reply_to()` copies the parent's anchor to make that
class of bug unrepresentable.

**State is keyed by repo *and* slug.** Slugs are not unique across checkouts, so
hashing the slug alone would let two repositories share — and corrupt — one
ledger.

**Debounce before firing.** The pending set must be stable across two polls, so
a burst of comments produces one wake instead of one per comment.

## Performance note

The daemon polls, so process spawns in its hot loop are the only place execution
cost matters in this collection. The shell version spawned `python3` at 11 sites,
two of them *per iteration* of the submit-confirmation loop — about 68 ms of
overhead on a 100 ms step, inflating it to ~168 ms. All of those are now
in-process calls: the measured per-iteration overhead is 0.18 ms, so the poll
step is the step it claims to be.

## Tests

```
bash ../tests/test-review-frontdoor.sh     # argv validation, anchor inheritance
python3 ../tests/test_review_marks.py      # mark coverage and freshness gate
python3 ../tests/test_review_close.py      # close approval classification
bash ../tests/test-tuicr-watch.sh          # transport, selection, portability
bash ../tests/test-refresh-review-refs.sh  # ref resolution, ff, refusals
bash ../../tests/test-portability.sh       # repo-wide guards
```
