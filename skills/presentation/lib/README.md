# `presentation/lib`

Python 3.11+, standard library only, except `feedback_server.py`. Shared
primitives come from [`../../_lib/skillkit`](../../_lib/README.md).

## `presentation.py <command> <doc.md> …`

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
The legacy directory name preserves conversations from before the skill split.
Session closure signals the end of discussion, never workflow approval.

## `feedback_server.py`

Stdio MCP server `presentation-feedback`, run from the plugin's `mcp.json` via
`uv run --project skills/_lib` (needs `mcp`). Adds `connect_review(doc)` —
requires a running `presentation.py open <doc>` — to the common tools from
[`skillkit.feedback`](../../_lib/README.md#review-feedback-contract), using the
server's local `/agent/*` routes. `reply` answers the thread's current human
tail. While `wait_for_feedback` blocks, the viewer shows the agent as waiting.
Closure is the session's closed flag; it never implies approval.

## `server.py`

`Session` holds the rendered document, threads and agent presence behind one
condition variable. A watcher re-renders on mtime change and relocates anchors.
HTTP routes: `/api/state` (browser long-poll), `/api/comment`, `/api/close`,
and `/agent/{wait,read,post,close}`. All `/api` and `/agent` routes require the
`X-Token` header. `/widget/<hash>` serves a widget's HTML without the token,
under a CSP with `sandbox allow-scripts` and no network sources; comments on a
widget may carry its reported `state` (≤2000 chars of JSON).

## `document.py`

`render(markdown)` returns the title, HTML and ordered units (source line, kind,
plain text, diagram labels) and widget bodies keyed by content hash. `relocate(anchor, units)` moves an anchor to the
unique exact match of its quoted units or marks it outdated.
`message_html(text)` renders a comment's inline spans, lists, fenced code and
display math; the server adds it to each message as `html`.
Code fences retain `data-lang`; the browser's local Highlight.js bundle colours
both page and comment code, preserving source-line units and selection anchors.
Numeric `[^N]` citations render as links to a generated final Sources section;
`[^N]: …` defines a source. Diagram, widget and math fences accept citations in
their opening info. Badge digits stay outside selectable anchor text. Missing
definitions produce a visible warning entry; duplicate definitions raise
`ValueError`, reported by the server while retaining the last valid revision.

## `latex.py`

`to_mathml(source, display=False)` converts a common LaTeX math subset to a
MathML Core `<math>` element, which browsers render and print natively. Unknown
commands become `<merror>`; malformed input never raises.
