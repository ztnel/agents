# Live watch — daemon reference

`lib/tuicr_watch.py` is a small non-LLM daemon. It polls a tuicr review
session's persisted comments and wakes a Copilot CLI session whenever a comment
is awaiting a response. `lib/watch_up.py` manages its lifecycle.

The behavioural half — what the woken agent must do — is the **Wake contract**
in [`../SKILL.md`](../SKILL.md), not this document.

## How it works

- **It never reads the tuicr TUI.** tuicr persists every comment to a session
  file; the daemon polls the same data through `tuicr review comments`. tuicr
  does not even need to be visible in tmux.
- **Pending = awaiting a response.** A comment is pending when its type is not
  ignored (default ignore: `reply`), **and** no `reply` exists at the same
  anchor with a later `created_at`, **and** it has not already been delivered.
  Because tuicr has no parent/thread field, anchor + timestamp *is* the thread.
- **Delivery ledger, not a seen-list.** Deliveries are recorded per comment id
  with an attempt count. A comment that was delivered but never got a reply is
  **re-armed** after `--rearm` seconds (default 900) up to `--max-attempts`
  (default 3), so a wake the agent silently dropped is retried instead of lost.
  Ids that disappear from the session are pruned, so the ledger stays bounded.
- **Debounce.** The pending set must be stable across two polls before firing,
  so a burst of comments produces one wake.
- **Verified submit.** The prompt is loaded into a tmux buffer and pasted (never
  typed key-by-key). The CLI enables CSI-u keyboard encoding, so a named tmux
  `Enter` can be ignored by an inactive window: the daemon sends the terminal
  focus-in sequence to the target pane *without switching the human's window*,
  submits raw CSI-u Enter (`ESC [ 13 u`), then restores focus-out. If the token
  is still in the input box after an attempt, it falls back to a named `Enter`.
  The daemon still wakes a live session even when the input box already holds
  text.
- **The oracle is the persisted event, never the screen.** Success means the
  wake's deterministic token appears in a `user.message` event in the target
  session's `events.jsonl`. `capture-pane` is diagnostic only. Scanning starts
  from the file size recorded just before submitting, so confirmation stays
  cheap on a long session.
- **Exact target binding, re-checked every time.** The pane is resolved *from*
  the CLI session id (a session lock PID sharing process ancestry with the
  pane), and re-verified immediately before every delivery. If the agent exits
  and a shell reclaims the pane, delivery is refused — otherwise the prompt
  would be typed into a shell and submitted.
- **Singleton.** A lock file per watched (repo, session) refuses a second
  daemon, which would double-wake the agent.
- **Read failures are not "no comments".** If `tuicr review comments` fails, the
  daemon warns and retries rather than treating the session as empty.
- **No silent backlog.** Every unanswered comment is delivered, including ones
  written before the daemon attached. Seeding a first-run backlog as "seen" is
  opt-in (`--seed-existing`) because it can only ever suppress *unanswered*
  comments — answered ones are already excluded — and the ledger is keyed by
  (repo, session), so each new review cycle starts empty and would re-seed.
- **The count is total outstanding work.** The prompt reports every comment
  awaiting a response, not just the ones that triggered the wake, so an earlier
  comment held back by `--rearm` is never understated.

```
 tmux session
 ├─ pane: the agent's CLI      ← paste + submit target (resolved from session id)
 ├─ window: tuicr TUI          (human writes comments)
 └─ tuicr-watch (detached)     → poll → deliver → confirm in events.jsonl
```

## The wake prompt

```
tuicr wake <token>: 2 pending comments in review session <slug> (<repo>). Follow the tuicr skill Wake contract.
```

That is the whole prompt, by design:

- **It carries no instructions.** Behaviour lives in the skill, so it cannot
  drift from the skill and is identical for every agent.
- **It carries no comment text.** The human's words are never echoed back into
  the agent's input box.
- **The token is required.** It is both the acceptance oracle matched in
  `events.jsonl` and the idempotency key that prevents a duplicate wake being
  processed twice.

## Invocation

`watch_up.py` is the supported entry point — it detaches the daemon, tracks a
pidfile, and is idempotent per `--name`.
If the pidfile already names a live watcher for that `(repo, session)`, it is
stopped and replaced with a fresh daemon for the new Copilot session.

```bash
~/.agents/skills/tuicr/lib/watch_up.py \
  --repo /path/to/repo --session <slug> \
  --cli-session "<full CLI session id from your session context>" \
  --name "<label>"
```

Omit `--session` to use the repo's single active session (it refuses, listing
candidates, if ambiguous). Do **not** pass `$TMUX_PANE`: when the caller was
launched via `tmux new-window`, that variable holds the *new* window's pane.
Pass `--cli-pane` only to override a correct automatic resolution.

Lifecycle:

```bash
watch_up.py --list                                   # names, pids, live/dead
watch_up.py --stop <name>
watch_up.py --stop-all --prefix <p> --exclude <substr>
```

Run the daemon directly (`lib/tuicr_watch.py`) only for debugging.

## Options (`tuicr_watch.py`)

| Option | Meaning | Default |
|--------|---------|---------|
| `--repo <path>` | Checkout used to resolve the session | `.` |
| `--session <slug>` | Session slug; omit to use the active one | (active) |
| `--cli-session <uuid>` | CLI session to wake | (resolved from pane) |
| `--cli-pane <pane>` | Pane override | (resolved from session) |
| `--interval <s>` | Poll interval | `1.5` |
| `--submit-tries <n>` | Max submit attempts | `3` |
| `--submit-step <s>` | Confirmation poll interval | `0.1` |
| `--event-timeout <s>` | Confirmation timeout per attempt | `3` |
| `--queue-timeout <s>` | Wait after the prompt leaves the box | `300` |
| `--rearm <s>` | Re-deliver an unanswered comment after this long; `0` disables | `900` |
| `--max-attempts <n>` | Deliveries per comment before giving up | `3` |
| `--ignore-type <t>` | Comment type to ignore (repeatable; replaces the default) | `reply` |
| `--replay` | Ignore delivery history; treat all current comments as pending | off |
| `--seed-existing` | On a first run, mark the existing backlog as seen instead of delivering it | off |
| `--once` | Single fire cycle, then exit | off |
| `--dry-run` | Print what would be sent; touch no tmux or state | off |
| `--state-dir <p>` | Ledger/lock/log dir | `$XDG_STATE_HOME/tuicr/watch` |
| `--session-state-dir <p>` | CLI session-state root | `$COPILOT_SESSION_STATE_DIR` or `~/.copilot/session-state` |

Ignoring extra types is how a workflow keeps machine-generated comments from
waking the agent — for example a mirror that stamps synced comments with their
own type. Passing any `--ignore-type` replaces the default, so include `reply`
explicitly if you still want replies ignored.

## Verifying without touching tmux

```bash
~/.agents/skills/tuicr/lib/tuicr_watch.py \
  --repo /path/to/repo --session <slug> --once --replay --dry-run
```

## Platform notes

Linux, macOS, and WSL. tmux is required (it is the wake transport) and must
support `send-keys -H`; the daemon preflights this and says so if not.
Everything else uses `bash`, `git`, `python3`, and POSIX-common tools only — no
`sha1sum`, `setsid`, or `rg`, none of which exist on a stock macOS. State
honours `XDG_STATE_HOME`, and the CLI session root honours
`COPILOT_SESSION_STATE_DIR`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `not inside a tmux session` | Launch inside tmux, or use `--dry-run` to test detection only |
| `send-keys lacks the -H flag` | Upgrade tmux; the CSI-u submit cannot be expressed without it |
| `no live tmux pane hosts Copilot session` | That session is not running in tmux, or its id is wrong — use the full id from the session context |
| `no longer hosts session …; not delivering` | The agent exited and a shell took the pane. Restart the agent, then the watcher |
| `another tuicr-watch … already watches this session` | A daemon is already running; use `watch_up.py` (it reuses a live one) |
| `could not resolve a single active session` | Pass `--session <slug>` from `tuicr review list --repo <repo>` |
| Nothing fires after a comment | Check the comment's type is not ignored and the slug matches; check the log in the state dir |
| `could not read comments … consecutive failures` | The session was deleted or `tuicr` is failing; the daemon is deliberately *not* treating this as "no comments" |
| Wake stays in the input box | The CLI ignored both CSI-u and named Enter — check the pane/session pairing and the log |
| Wake left the box but no event | The agent is mid-turn; the daemon waits `--queue-timeout` without resubmitting. Raise it for long turns |
| Input box holds other text | The daemon still wakes the session; check the pane/session pairing and log if nothing appeared |
| A comment was woken twice | Expected after `--rearm` if no reply was ever posted. The Wake contract requires idempotent handling |
| Backlog delivered on attach | Expected: comments written before the daemon started are real outstanding work. Pass `--seed-existing` to adopt a session's backlog as already-handled |
| Count higher than the comments you just wrote | Expected: the count is *total* unanswered, including earlier comments still awaiting a reply |

## When not to use

- A one-off review you will read manually — use the CLI directly.
- Any flow that should commit, push, or write to a remote forge — out of scope
  by design.
