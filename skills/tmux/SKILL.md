---
name: tmux
description: "Discover, open, verify, focus, resize, inspect and close tmux panes and windows by exact pane ID, returning JSON with stable session, window and pane IDs. Launches a command in a detached pane, confirms it actually started, and reports explicitly when tmux is missing or the caller is outside tmux so the workflow can fall back to direct launch instructions. Never touches unnamed panes or global tmux settings. Use when a workflow needs a terminal for a long-running or interactive tool (a review UI, server, watcher, editor), when checking whether tmux is available, or when inspecting or cleaning up panes it opened. Application skills say what to run; this skill decides where it runs."
allowed-tools: bash
---

# `tmux` — terminal panes for agent workflows

Owns terminal mechanics only: where a command runs and whether it started. It
knows nothing about what runs there. Application skills (for example `tuicr`)
expose plain commands that work in any terminal; a custom agent composes them
with this skill when tmux is available and gives the human direct launch
instructions when it is not.

It does not send keystrokes or prompts into panes, and it never changes
server, session or global options.

## Commands

`$SKILL_DIR` is the directory containing this SKILL.md. Run scripts by that
absolute path, directly; they have shebangs, so never prefix `python`.

```bash
T=$SKILL_DIR/lib/tmux_pane.py
$T status                                   # {"available", "inside", "pane", "version"}
$T list [--scope all|session|window] [--target T]
$T open --cwd DIR [--window NAME | --split h|v [--size 40%]] [--target %PANE] \
        [--env NAME]... [--expect PROCESS] [--timeout 10] [--settle 0.3] \
        -- COMMAND [ARG]...
$T focus   --pane %PANE
$T resize  --pane %PANE [--width N|N%] [--height N|N%]
$T capture --pane %PANE [--lines N]
$T close   --pane %PANE [--allow-self]
```

Every command prints one JSON object. Pane descriptions include `pane_id`,
`window_id`, `session_id`, names, indexes, `pane_pid`, size, focus state,
`pane_current_command` and `pane_current_path`.

| exit | meaning |
| --- | --- |
| 0 | ok |
| 2 | usage error, a target that is not an exact `%N` pane ID, or closing the calling pane |
| 3 | tmux is not installed, or the caller is not inside tmux |
| 4 | tmux refused, or the named pane does not exist |
| 5 | the opened pane exited, or `--expect` did not appear before `--timeout` |

## Workflow

1. `status` → if `available` and `inside` are both true, continue; otherwise
   tell the human the exact command to run in a terminal of their own.
2. `open` → by default a new detached window in the calling pane's session;
   `--split` splits `--target` instead. The command is an argument vector,
   quoted once for tmux. `--env NAME` copies a variable from the caller as a
   literal value; unset names are skipped.
3. Verify launch: `open` exits 0 only when the pane is alive after `--settle`
   and, with `--expect`, a process of that name runs in the pane's process tree.
   On exit 5 the pane is left open for diagnosis; `capture` it, then `close` it.
4. Record the returned `pane_id`. Later `focus`, `resize`, `capture` and
   `close` calls take only that ID.

## Gotchas

- `open` never steals focus: the human keeps working where they are. Tell them
  the window name instead; call `focus` only when they ask to be taken there.
- New panes inherit the tmux server's environment, not the caller's shell.
  Forward what the command needs with `--env`, for example `EDITOR`.
- `pane_current_command` names the pane's foreground process (often the
  launcher, such as `Python`), not every child. Use `--expect` to verify a
  child process started.
- `$TMUX_PANE` in a pane opened here names that new pane, not the agent's pane.
  Pass any identity the launched command needs explicitly.
