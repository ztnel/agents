# `tuicr/lib` — review-session tooling

Scripts backing the `tuicr` skill: opening a review window, keeping its refs
honest, writing the close report, and serving review feedback to agents over
MCP.

Scripts are executable Python entry points using only the standard library,
except `feedback_server.py`, which needs `mcp` via
`uv run --project skills/_lib`.
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

#### Recovering a changed file type

Some tuicr binaries retain a file's old `status` after it changes from modified
to deleted, or the reverse. The gate reports `unreviewed`, not a permanent
`stale`, and does not trust a retained `reviewed=true` mark.

Reload the diff with `:e`. If the binary refreshes status and clears the mark,
review and re-mark the current file normally. Otherwise:

1. The human unmarks the affected file and saves with `:w`.
2. Run `review.py --repo D --session S reviewed --gate` to observe the unreviewed mark; exit 5 is expected.
3. The human reviews the current diff, re-marks the file, and saves with `:w`.
4. Run the gate again; the unchanged file now passes despite the old status.

The gate records this transition in skill-owned
`XDG_STATE_HOME/tuicr/transitions` state, scoped to checkout, review session,
`HEAD`, and staged/worktree selection. Type, diff, or content changes require
a new unmark/re-mark observation. Saving unrelated comments or reopening alone
cannot clear the block. Agents must not toggle marks or edit, replace, or delete
persisted tuicr session files for recovery.

Exits non-zero with a message naming the valid subcommands when one is omitted,
and lists the available comment ids when `--to` names an unknown one.

### `tuicr_up.py [directory]`

Launches tuicr in a new tmux window over a freshly-resolved revset, focuses
it, prints `Review session: <slug>`, deletes any stale close report for that
session, and blocks until tuicr exits. Run it as an attached background
command. It then evaluates review marks against the unchanged `HEAD`, checks
explicitly registered questions to the human, and persists the JSON verdict
(`approved`/`incomplete`/`aborted`) to
`$XDG_STATE_HOME/tuicr/reviews/<hash>.json` (`report_path()`), which
`feedback_server.py` delivers as the closure event. A killed or failed window
is always `aborted`.
The launcher leaves tuicr attached to the pane's terminal, so `e` can open a
terminal editor such as Neovim. Exported instructions use tuicr's clipboard
path rather than `--stdout` capture.
New windows receive the caller's `EDITOR` and `VISUAL` when set, without
changing tmux's global environment. Unset variables retain tmux defaults;
existing review windows are not modified.

Reviewed regular files carry `content_sha256` fingerprints in `marks.files`.
Consumers verify their own inputs against those fingerprints before acting.
The close report's `approval` field is a provider-neutral `agents.approval/v1`
receipt from `skillkit.approval`. Pass that receipt to capability consumers;
keep tool-specific marks, exit status, and question diagnostics in this adapter.

### `review.py register-question / resolve-question`

Register a model-authored question to the human by its exact comment ID:

```console
review.py --repo D --session S register-question --comment-id QUESTION_ID
review.py --repo D --session S resolve-question --comment-id QUESTION_ID --answer-id ANSWER_ID
```

Only a newer, non-empty authorless comment can be linked as a human answer.
Model-authored replies cannot resolve questions. Edits or deletion invalidate
the binding; re-register edited questions and link a new human answer. Orientation
types (`description`, `review-note`) cannot be registered. The registry is
skill-owned state under `XDG_STATE_HOME/tuicr/questions`, keyed by checkout
and session; persisted tuicr sessions and human marks are never modified.
Registered questions are never pending feedback, but still block approval.

Configured by environment, not flags: `TUICR_WINDOW_NAME` (default `tuicr`),
`TUICR_BASE_REF` (default: **the remote's own default branch**, never a
hardcoded name), `TUICR_HEAD_REF` (default `HEAD`), `TUICR_REMOTE` (default
`origin`).

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

### `feedback_server.py`

Stdio MCP server `tuicr-feedback`, run from the plugin's `mcp.json`. Adds
`connect_review(repo, session=None)` (session is a slug; default the repo's
active session) to the common tools from
[`skillkit.feedback`](../../_lib/README.md#review-feedback-contract):
`read_review`, `receive_feedback`, `acknowledge_feedback`, `reply`,
`wait_for_feedback`. Pending comments exclude agent `reply`, `description`,
and `review-note` types, registered questions, and comments with a later reply
at the same anchor. `reply` posts a `--type reply` at the original's anchor.
Closure is the close report: `closure = {"verdict", "report"}` for sessions
launched by `tuicr_up.py`; otherwise `closed` is `null` (unknown). Closure
never implies approval — read the report.

## Design criteria

**Threading is positional.** tuicr comments carry no parent id; a reply attaches
to a comment only when `(path, start_line, end_line, side)` match exactly.
Pending detection uses that same tuple to decide "answered", so a mis-anchored
reply leaves the comment pending and it is redelivered.
`skillkit.tuicrio.reply_to()` and the feedback server's `reply` copy the
parent's anchor to make that class of bug unrepresentable.

**Closure is authoritative only from the launcher.** The close report is the
single source of the verdict; a session opened another way reports unknown
closure rather than guessing.

## Tests

```
bash ../tests/test-review-frontdoor.sh     # argv validation, anchor inheritance
python3 ../tests/test_review_marks.py      # mark coverage and freshness gate
python3 ../tests/test_review_close.py      # close approval classification
uv run --project ../../_lib python ../tests/test_feedback_server.py  # MCP conformance
bash ../tests/test-refresh-review-refs.sh  # ref resolution, ff, refusals
bash ../../tests/test-portability.sh       # repo-wide guards
```
