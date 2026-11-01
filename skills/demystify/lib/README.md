# `demystify/lib`

Python 3.11+, standard library only. Shared primitives come from
[`../../_lib/skillkit`](../../_lib/README.md).

## `demystify.py <command> <doc.md> …`

Entry point. Prints JSON on stdout; errors use the `skillkit` format and exit
codes (`2` usage, `4` no server for the document).

| Command | Contract |
|---|---|
| `open [--no-browser \| --browser] [--restart]` | Start or reuse the document's server; opens a tab only on start (`--browser` forces one). `--restart` replaces a running server on the same port and token, so open tabs reconnect without a new one. Returns `url`, `reused`, `pid` |
| `wait --model M [--timeout S]` | Block 1–300 s; returns `status` (`feedback`, `timeout`, `closed`) and `pending` threads with `context`. Returns `closed` after the session ended even though the server has exited |
| `read` | All threads with `context`, plus `pending` |
| `reply --model M --thread T (--body \| --body-file)` | Append an agent message to a thread |
| `ask --model M (--line N [--end N] [--element L \| --text T] \| --document) (--body \| --body-file)` | Start an agent thread |
| `close` | End the session; waits return `closed` and the server exits |
| `list` | Running sessions |
| `serve` | Internal: the detached server process started by `open` |

State lives in `$XDG_STATE_HOME/demystify/<hash-of-doc-path>/`:
`session.json` (port, token, pid; mode 0600), `threads.json` (conversation and
closed flag) and `server.log`.

## `server.py`

`Session` holds the rendered document, threads and agent presence behind one
condition variable. A watcher re-renders on mtime change and relocates anchors.
HTTP routes: `/api/state` (browser long-poll), `/api/comment`, `/api/close`,
and `/agent/{wait,read,post,close}`. All `/api` and `/agent` routes require the
`X-Token` header.

## `document.py`

`render(markdown)` returns the title, HTML and ordered units (source line, kind,
plain text, diagram labels). `relocate(anchor, units)` moves an anchor to the
unique exact match of its quoted units or marks it outdated.

## `latex.py`

`to_mathml(source, display=False)` converts a common LaTeX math subset to a
MathML Core `<math>` element, which browsers render and print natively. Unknown
commands become `<merror>`; malformed input never raises.
