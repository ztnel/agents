---
name: presentation
description: "Present a Markdown document on a live, read-only local page with anchored comments, agent replies, live updates and PDF export. Supports inline SVG diagrams, LaTeX math and sandboxed widgets. Use when presenting a document for interactive discussion, including an alignment brief or explainer, or when opening, reading, replying to or waiting for comments on this surface. Content and workflow decisions belong to the invoking agent; closing a page is not approval."
---

# `presentation` — discuss a live document

Owns the presentation surface: a Markdown document the agent writes, served
read-only to the human with line and diagram-element comments.
The invoking agent owns content, sourcing requirements, brevity and workflow
decisions. This skill does not define teaching or development policy.
Closing a page ends discussion; it does not approve implementation, code or
publication.

`$SKILL_DIR` is the directory containing this SKILL.md. Run scripts by that
absolute path, directly; they have shebangs, so never prefix `python`.
The entry point is `$SKILL_DIR/lib/presentation.py` (Python 3.11+,
stdlib only). Every command prints JSON. Pass the same document path to each.

## Workflow

Run this loop until the session is closed.

1. **Build** the page: write `<topic>.md` in your session files folder, not the
   workspace (see *Authoring*).
2. **Open** it → verify the JSON has a `url`, then start waiting at once. The
   first `open` starts the server; its tab opens when you first wait, so the
   human never comments before you listen. Later calls reuse that tab. Pass
   `--browser` only if the human closed the tab.
   ```bash
   $SKILL_DIR/lib/presentation.py open <doc.md>
   ```
3. **Wait** for comments, passing your concrete model id (shown to the human).
   Prefer the MCP loop below; this CLI is the fallback:
   ```bash
   $SKILL_DIR/lib/presentation.py wait <doc.md> --model <model-id> --timeout 300
   ```
   - `feedback`: answer every entry in `pending`. Each carries `thread`, the
     anchored `context` text and the human's `messages`.
   - `timeout`: no comments yet; wait again.
   - `closed`: the human ended the session; stop the loop and summarise.
4. **Reply** in the thread, then revise the document if the explanation was
   unclear or wrong. The page reloads on save; comments follow their text.
   ```bash
   $SKILL_DIR/lib/presentation.py reply <doc.md> --model <model-id> --thread <id> --body "…"
   ```
   Use `--body-file` for long or quoted text. Replies render `**bold**`, `*em*`,
   `` `code` ``, lists, fenced code and `$…$`/`$$…$$` math. Reply before editing so the
   human sees the answer, then say in the reply what you changed.
5. Go to 3.

**MCP loop (preferred).** After `open`, call `connect_review(doc)` on the
`presentation-feedback` MCP server, then loop on
`wait_for_feedback(review_id, author=<model-id>)`. Each batch holds ordered
events: answer each non-`superseded` `message` with
`reply(review_id, message_id, message_version, verdict, content, author, task)`
(it answers the thread's current tail; `verdict` is `accepted` with a one-line
`task`, `declined` or `answered`), skip `withdrawn`, and stop on `closure` —
closure is not approval. Then `acknowledge_feedback(review_id, batch_id)`; an
unacknowledged batch replays, also after a server restart, so never duplicate a
reply. Revise the document as in step 4: presentation work is not batched, so
apply accepted changes immediately.

A server with no tab, bridge or agent request for 15 minutes exits on its own;
threads persist and `open` resumes them. The bridge stops polling at closure.

The green dot means an agent is blocked in `wait`; amber means it replied or
stopped waiting in the last 10 minutes. Waiting keeps your turn busy, so stay
in the loop rather than ending the turn.

Other commands:

| Command | Use |
|---|---|
| `read <doc.md>` | All threads, contexts and pending comments |
| `ask <doc.md> --model M --line N [--end N] [--element L \| --text T] --body …` | Start a thread on lines, a diagram part or exact text on those lines, e.g. a check-for-understanding question |
| `ask <doc.md> --model M --document --body …` | Start a whole-document thread |
| `close <doc.md>` | End the session (normally the human does this with `:q`) |
| `list` | Running sessions and their URLs |

Exit code `4` means no server is running for that document: run `open` again.
Reopening resumes saved conversations. After the skill's code changes, `open --restart`
reloads the server; the human's tab reconnects, and a page reload picks up new assets.

## Authoring

Write a standalone document, not a chat transcript. Diagrams, math, widgets and
citations are available, not mandatory.

- **One sentence per source line.** Each source line is one selectable line on
  the page; paragraphs still render as flowing text.
- **Citations.** Attach `[^1]` to a sentence and define
  `[^1]: [Title](https://…)` on its own line. Reuse numbers. For diagrams,
  widgets and fenced math, put `[^1]` after the opening fence's language/caption.
  Definitions become the final **Sources** section; an optional `## Sources`
  heading is relocated there. Missing definitions are flagged on the page;
  duplicates reject the revision. The invoking agent decides which claims need
  sources; citation rendering does not verify their accuracy.
- Supported Markdown: headings, paragraphs, `-`/`1.` lists (nestable), `>`
  quotes, tables, fenced code, `**bold**`, `*em*`, `` `code` ``, http(s) links.
  No raw HTML or images.
- **Code** fences take a language (`python`, `js`, `bash`, `cpp`, etc.) for local
  syntax highlighting, including in replies. Omitted languages are auto-detected;
  unsupported ones stay plain text.
- **Math** is LaTeX: `$…$` inline, `$$…$$` display (one or more lines) or a
  ```` ```math ```` block. It renders as native MathML. The human can select and
  search inline math; a display equation is one stop, commented as a whole. Common commands work: scripts, `\frac`, `\sqrt`, Greek letters,
  operators, functions, accents, `\mathbf`/`\mathbb`, `\left…\right`, `\text`, and
  `matrix`/`pmatrix`/`cases`/`aligned`. Unknown commands render in red. Write `\$`
  for a literal dollar.
- **Diagrams** are fenced ```` ```svg ```` blocks with inline SVG. Set `viewBox`.
  Put `data-label="name"` on each `<g>` the human may want to discuss. The human
  cycles parts with `h`/`l` and can search the diagram's `<text>`; selecting a part's text anchors to that part. A diagram's line for `ask --line` is its opening fence.
  Scripts and event handlers are stripped.
- Animate and add interactivity with SMIL (`<animate>`, `<set begin="id.click">`).
  Use ids without `-`, which SMIL parses as a time offset. The PDF captures one frame.
  Page CSS colours `<text>`; override with `style="fill:…"`, not `fill=`.
- **Widgets** are fenced ```` ```widget Caption ```` blocks of HTML, CSS and
  inline `<script>` for sliders, buttons and live plots. Each runs in a sandboxed
  frame: no network, no external scripts, no access to the page. Height fits the
  content. Helpers: `dm.plot(el, {x: [lo, hi], y?, series: [{f, color?, label?}],
  xlabel?, ylabel?, marks?})` draws an SVG plot; `dm.state({k: v})` adds values.
  Give inputs a `name` or `id`: their current values reach you with the comment.
  A widget is one cursor stop; the human presses `Enter` to interact and `Esc`
  to return. The PDF captures its current state. Widget lines must not start with
  a code fence.
- The page is dark. Use the built-in classes instead of hard-coded colours:
  `box` (with optional `accent`, `ok`, `warn`, `alt`), `edge` (optional `accent`),
  `label`, `muted`, `arrow` (for marker paths). Plain `<text>` is already light.
- Rewriting a commented line detaches its thread (shown as *outdated*, still
  readable). Prefer adding clarifying lines over rewording the commented one.

## Gotchas

- Existing state directories, browser storage keys, widget message markers and
  the `dm` helper retain their legacy names to preserve saved conversations and
  authored widgets. Use `open --restart` to update an existing tab's assets.

- `--model` must be your real model id; it is the agent's name on the page.
- `wait` returns pending comments until each thread has an agent reply, so a
  second `wait` without replying returns the same feedback immediately.
- The browser URL contains the session token. Keep it local.
