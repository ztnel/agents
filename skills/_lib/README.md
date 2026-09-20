# `_lib/skillkit` — shared primitives for the agent skills

The skills are **glue**: they drive `git`, `tmux`, `tuicr`, `az` and `acli` and
shuttle JSON between them. `skillkit` holds the primitives they all need, so a
portability fix lands **once** instead of seventeen times.

## Why this exists

Every portability bug these skills have hit was an *environment* bug, not a
logic bug — GNU vs BSD coreutils flags, and bash 3.2 on macOS lacking `declare
-A` and `mapfile`. Python has neither class of problem. The rules that keep it
that way:

| Never shell out for | Use instead |
| --- | --- |
| `sha1sum` / `shasum` | `paths.short_hash()` (`hashlib`) |
| `stat -c` / `stat -f` | `paths.file_size()` (`os.stat`) |
| `date -d` / `date -v` | `datetime` |
| `grep` / `sed` / `awk` | `re` |
| `setsid` / `nohup … &` | `proc.detach()` (`start_new_session=True`) |
| `mktemp` flag variants | `tempfile` |

Argument vectors are **lists, never shell strings**, so quoting and word
splitting cannot go wrong.

### Scope: Linux, macOS, WSL

`tmux` is an assumed hard dependency — it is the transport that carries a wake
into a running Copilot CLI pane. It has no native Windows port, so **native
Windows is out of scope by design**; WSL is the supported path there. Nothing
else in this package is POSIX-bound, so if that dependency ever changes, the
remaining surface is already portable.

## Importing

Entry points live at `~/.agents/skills/<skill>/lib/<name>.py`, so `_lib` is
always two levels up. Start each one with:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
```

Set `SKILLS_ROOT` to override the location when a skill is vendored elsewhere.

## Modules

| Module | Responsibility |
| --- | --- |
| `proc` | `run()`, `detach()`, `require()`, PID liveness and ancestry |
| `cli` | `==>` / `WARN:` / `ERROR:` output, `KEY=VALUE` metadata, `run_main()` |
| `paths` | XDG dirs, local scratch paths, `short_hash()`, atomic writes, tolerant JSON reads |
| `tmuxio` | Panes, windows, buffers, hex key delivery, focus reporting |
| `tuicrio` | Typed `tuicr` client: `Anchor`, `Comment`, `reply_to()` |
| `copilot` | CLI session state, pane binding, `events.jsonl` acceptance oracle |
| `gitio` | Typed `git` wrapper, incl. `default_remote_branch()` |
| `lock` | `PidFile` singleton guard, compatible with the shell pidfile format |
| `errors` | `SkillError` and friends, each carrying its process exit code |

## Design contracts worth knowing

**Exit codes are part of the API.** The shell scripts signalled failure with
distinct codes (`3` missing dependency, `4` bad tmux/session target, `8` lock
held) and the test suites assert on them. `SkillError` carries a `code`, and
`cli.run_main()` maps it to the process exit status, so those contracts survive
the port.

**Output format is part of the API.** `==> `, `WARN: `, `ERROR: ` and
`KEY=VALUE` blocks are consumed by callers and tests. `cli.emit()` additionally
`shlex.quote`s values, so `eval "$(script.py …)"` survives paths with spaces —
a case the hand-rolled shell emitters got wrong.

**tuicr threading is positional.** Comments have no parent or thread id; a reply
attaches to a comment only when `(path, start_line, end_line, side)` match
*exactly*. Omitting `--end-line` does not mean "unset" — it silently files the
reply at `start_line..start_line`, which on a multi-line comment is a different
anchor, so the reply neither threads nor marks the comment answered, and a
watcher re-delivers it until its attempt budget is spent.

`tuicrio.reply_to()` takes the **parent `Comment`** and copies its `Anchor`
rather than accepting loose line numbers. That makes the most common
mis-anchoring bug *unrepresentable* rather than merely documented.

**A tuicr failure is not "no comments".** `tuicrio.comments()` raises rather
than returning `[]` on a transport failure. Conflating the two would leave a
watcher reporting healthy while silently ignoring the human forever.

**Acceptance is proven from persisted events, never from the screen.**
`copilot.events_contain()` scans `events.jsonl` from a byte offset. Terminal
rendering (`tmuxio.capture`) is only ever a diagnostic.

**Pane binding is re-checked before every injection.** If the agent exits and a
shell reclaims the pane, pasting a prompt and pressing Enter would execute it as
a shell command. `copilot.pane_still_bound()` is a safety check, not an
optimisation, and an ambiguous match is always an error — never a guess.
