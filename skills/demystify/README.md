# demystify

Agent Skill for explaining concepts on a live, read-only page that mixes text
and SVG diagrams, with Vim-driven comments the agent answers in place. The
finished page saves as a PDF.

## Public API

`lib/demystify.py` (see [`lib/README.md`](lib/README.md)) opens a document and
lets an agent `wait`, `read`, `reply`, `ask` and `close`. `SKILL.md` defines the
agent loop and authoring rules.

## Keys

Modes are explicit and always shown in the status line: `NORMAL`,
`VISUAL LINE`, `INSERT`, `COMMAND`, `SEARCH`, `CLOSED`. The pane (`DOC` or
`THREADS`) is shown beside it, so a key never changes meaning silently.

| Keys | Action |
|---|---|
| `j` `k` · `5j` | Move to the character above or below on screen; a diagram is one stop; counts repeat |
| `gg` `G` · `42G` | Top, bottom, source line 42 |
| `Ctrl-d` `Ctrl-u` · `Ctrl-f` `Ctrl-b` · `zz` | Half page, full page, centre cursor |
| `[[` `]]` | Previous or next heading |
| `h` `l` · `w` `b` `e` · `0` `^` `$` | Move by character, word, line edge; inside diagram text and math too (`h` `l` cycle parts of a text-free diagram) |
| `v` · `V` · `o` | Visual character or line selection; swap its end |
| `c` · `C` | Comment on line, selection (diagram text and math included) or diagram part; on the whole document |
| Click · `Shift`+click | Move the cursor and open the thread under it; extend a character selection |
| `r` · `Enter` | Reply to the thread on this line; show it in the panel |
| `m` `M` | Next or previous thread |
| `Tab` · `Esc` | Open the floating thread panel and switch focus to it (`j`/`k`, `r`, `Enter` there); close it and return to the document |
| `/` · `n` `N` · `:noh` | Search (smartcase); cycle matches; clear |
| `Enter` · `Shift+Enter` · `Esc` | In INSERT: send, newline, close the conversation keeping the draft |
| `:w` · `:q` · `:wq` · `:N` | Save PDF, end session, both, go to line N |
| `Esc` · `?` | Clear count, selection or part; help |

Commented text stays highlighted (amber awaiting the agent, green answered) and
is clickable. `Ctrl-w` is not bound because browsers close the tab on it.

## Design criteria

- **Lightweight.** Python stdlib server and vanilla JS; no build step, no
  vendored libraries. Diagrams are inline SVG rather than a rendering library.
- **Read-only for the human.** The page offers comments only; the agent owns
  the document and revises it on disk.
- **Comments follow text.** Threads anchor to source lines plus a quote of the
  selected lines. After a revision a thread moves to the unique exact match of
  its quote; otherwise it is marked outdated, never guessed.
- **Clean PDF.** `:w` uses the browser's print dialog with a print stylesheet
  that keeps the dark theme and hides cursor, comments and chrome.
- **Local only.** Loopback server with a per-session token, Host/Origin checks
  and a CSP that blocks inline script, including script inside SVG.

Theme and interaction conventions follow [ztnel/mmdr](https://github.com/ztnel/mmdr).

## Tests

```console
python3 skills/demystify/tests/test_document.py
python3 skills/demystify/tests/test_server.py
```
