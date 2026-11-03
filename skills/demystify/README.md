# demystify

Agent Skill for explaining concepts on a live, read-only page that mixes text
and SVG diagrams, with Vim-driven comments the agent answers in place. The
finished page saves as a PDF.

## Public API

`lib/demystify.py` (see [`lib/README.md`](lib/README.md)) opens a document and
lets an agent `wait`, `read`, `reply`, `ask` and `close`. `SKILL.md` defines the
agent loop and authoring rules.

## Keys

Modes are explicit and always shown in the status line: `NORMAL`, `VISUAL`,
`VISUAL LINE`, `INSERT`, `COMMAND`, `SEARCH`, `CLOSED`. The pane (`DOC` or
`THREADS`, or `COMMENT` while composing) is shown beside it.

| Keys | Action |
|---|---|
| `j` `k` · `5j` | Move to the character above or below on screen; a diagram is one stop; counts repeat |
| `gg` `G` · `42G` | Top, bottom, source line 42 |
| `Ctrl-d` `Ctrl-u` · `Ctrl-f` `Ctrl-b` · `zz` | Half page, full page, centre cursor |
| `{` `}` · `[[` `]]` · `gO` | Previous or next section heading; toggle the contents sidebar (also `:toc`) |
| `gd` · `2gd` | Jump to this sentence or diagram's cited source; second attached source |
| `K` (Shift+k) | Inspect attached references in a floating window; `j/k` scroll, Esc or `q` closes |
| `Ctrl-o` · `Ctrl-i` | Back or forward through source jumps (counts repeat) |
| `h` `l` · `w` `b` `e` · `0` `^` `$` | Move by character (continuing across a paragraph's sentences), word, line edge, inline math included; display math is one stop; on a diagram `h` `l` cycle its labelled parts |
| `v` · `V` · `o` | Visual character or screen-row selection (diagrams, display math and widgets whole); swap its end |
| `viw` · `vip` | Select a word without adjacent spaces; select a whole paragraph |
| `c` · `C` | Comment on line, selection (inline math included, or diagram text reached by search) or diagram part; on the whole document |
| Click · `Shift`+click | Move the cursor and open the thread under it; extend a character selection |
| `r` · `Enter` | Reply to the thread on this line; show it in the panel |
| `m` `M` | Next or previous thread |
| `Tab` · `Esc` | Open the floating thread panel and switch focus to it (`j`/`k`, `r`, `Enter` there); close it and return to the document |
| `/` · `n` `N` · `:noh` | Search (smartcase); cycle matches; clear |
| `Enter` · `Shift+Enter` · `Esc` | Comment INSERT: send, newline, enter Normal mode inside the text box |
| `:w` · `:q` · `:wq` · `:e` · `:N` | Save PDF, end session, both, reload the page, go to line N |
| `Enter` · `Esc` | On a widget: interact with it; return to the document |
| `Esc` · `?` | Clear count, selection or part; help |

Commented text stays highlighted (amber awaiting the agent, green answered) and
is clickable. `Ctrl-w` is not bound because browsers close the tab on it.

Comments open in Insert mode. In comment Normal mode, `i` inserts before the
cursor, `a` after it, and `A` at the current line end; `x` deletes a character
(or selection) and copies it to the clipboard. `p` pastes the clipboard once
after the cursor, replacing a selection if present.
`u` undoes a deletion, paste or Insert-mode session; counts repeat undo.
`h/j/k/l`, `w/b/e`, `0/^/$`, `gg/G` navigate (j/k use logical lines).
Use `v`/`V`, `viw`/`vip` to select, then `y` to copy to the clipboard;
`yy`, `yiw`, `yip` copy a line, word or paragraph. Counts repeat motions.
Esc clears a selection or closes the conversation with its draft kept;
Tab returns to the page.

Fenced code in the page and comments uses local syntax highlighting.
Name the language after the opening fence (e.g. `python`, `js`, `bash`, `cpp`);
unlabelled code is auto-detected, unknown languages stay plain text.

## Sources

Attach numeric citations to the claims they support: `The time constant is RC. [^1]`.
Define each source on its own line: `[^1]: [Title](https://example.com)`.
Use verified references for every factual claim; identify original calculations
and diagrams as derived from the cited model.

For diagrams, widgets or fenced math, add citations to the opening fence:
```` ```svg [^1] ```` or ```` ```widget Caption [^1][^2] ````.
Muted superscript numbers link to the final **Sources** section, including in PDFs;
diagrams place them in a small source caption below the drawing.
Click a number or press `gd` on its sentence or block; `2gd` chooses the second
attached citation. Inline code and math leave citation syntax literal.
`K` previews all attached reference titles, links and notes without moving the
cursor; it also works on a Sources entry. It does not fetch external pages.
`Ctrl-o` returns to the previous cursor and scroll position; `Ctrl-i` goes forward.
History includes citation clicks, keeps up to 100 jumps, and resets when the
document changes or the page reloads.
Missing definitions are visibly flagged; duplicate definitions reject the revision.
An optional `## Sources` heading is moved to the end with the numbered listings.

## Design criteria

- **Lightweight.** Python stdlib server and vanilla JS; no build step.
  Highlight.js 11.11.1 is vendored locally (BSD-3-Clause; see
  [`assets/highlight.LICENSE`](assets/highlight.LICENSE)); no CDN or runtime install.
  Diagrams use inline SVG.
- **Read-only for the human.** The page offers comments only; the agent owns
  the document and revises it on disk.
- **Comments follow text.** Threads anchor to source lines plus a quote of the
  selected lines. After a revision a thread moves to the unique exact match of
  its quote; otherwise it is marked outdated, never guessed.
- **Clean PDF.** `:w` uses the browser's print dialog with a print stylesheet
  that keeps the dark theme and hides cursor, comments and chrome.
- **Local only.** Loopback server with a per-session token, Host/Origin checks
  and a CSP that blocks inline script, including script inside SVG. Agent
  widgets run in `sandbox="allow-scripts"` frames with no network access.

Theme and interaction conventions follow [ztnel/mmdr](https://github.com/ztnel/mmdr).

## Tests

```console
python3 skills/demystify/tests/test_document.py
python3 skills/demystify/tests/test_server.py
node --test skills/demystify/tests/test_app.mjs
```
