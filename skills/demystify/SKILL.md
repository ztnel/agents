---
name: demystify
description: "Explain a concept to the human as a live, read-only explainer page that mixes Markdown text with inline SVG diagrams and LaTeX math, then converse on it: the human comments on lines or diagram parts with Vim keys, the agent waits for comments, replies with its model id, and revises the page until the human closes the session and saves a PDF. Use when the human asks to explain, teach, demystify, walk through or deep-dive a concept, design or system, wants a diagram-backed explainer, or wants to discuss an explanation interactively instead of in chat. For reviewing Mermaid diagram files use mermaid-review instead."
---

# `demystify` — converse on a live explainer

Owns explainer pages: a Markdown document the agent writes, served read-only to
the human with line and diagram-element comments. Not for code review (`tuicr`)
or Mermaid file review (`mermaid-review`).

The entry point is `lib/demystify.py` in this skill's directory (Python 3.11+,
stdlib only). Every command prints JSON. Pass the same document path to each.

## Workflow

Run this loop until the session is closed.

1. **Build** the page: write `<topic>.md` in the human's workspace or the session
   files folder (see *Authoring*).
2. **Open** it → verify the JSON has a `url`; tell the human it is open. A tab
   opens only when the server starts; later `open` calls reuse it without one, so
   test with `--no-browser` and pass `--browser` only if the human closed the tab.
   ```bash
   lib/demystify.py open <doc.md>
   ```
3. **Wait** for comments, passing your concrete model id (shown to the human):
   ```bash
   lib/demystify.py wait <doc.md> --model <model-id> --timeout 300
   ```
   - `feedback`: answer every entry in `pending`. Each carries `thread`, the
     anchored `context` text and the human's `messages`.
   - `timeout`: no comments yet; wait again.
   - `closed`: the human ended the session; stop the loop and summarise.
4. **Reply** in the thread, then revise the document if the explanation was
   unclear or wrong. The page reloads on save; comments follow their text.
   ```bash
   lib/demystify.py reply <doc.md> --model <model-id> --thread <id> --body "…"
   ```
   Use `--body-file` for long or quoted text. Reply before editing so the
   human sees the answer, then say in the reply what you changed.
5. Go to 3.

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

The human reads the page and saves it as a PDF, so write a standalone explainer,
not a chat transcript.

- **One sentence per source line.** Each source line is one selectable line on
  the page; paragraphs still render as flowing text.
- Supported Markdown: headings, paragraphs, `-`/`1.` lists (nestable), `>`
  quotes, tables, fenced code, `**bold**`, `*em*`, `` `code` ``, http(s) links.
  No raw HTML or images.
- **Math** is LaTeX: `$…$` inline, `$$…$$` display (one or more lines) or a
  ```` ```math ```` block. It renders as native MathML, so the human can select and
  search it. Common commands work: scripts, `\frac`, `\sqrt`, Greek letters,
  operators, functions, accents, `\mathbf`/`\mathbb`, `\left…\right`, `\text`, and
  `matrix`/`pmatrix`/`cases`/`aligned`. Unknown commands render in red. Write `\$`
  for a literal dollar.
- **Diagrams** are fenced ```` ```svg ```` blocks with inline SVG. Set `viewBox`.
  Put `data-label="name"` on each `<g>` the human may want to discuss. The human
  can select and search the diagram's `<text>`; selecting a part's text anchors to that part. A diagram's line for `ask --line` is its opening fence.
  Scripts and event handlers are stripped.
- Animate and add interactivity with SMIL (`<animate>`, `<set begin="id.click">`).
  Use ids without `-`, which SMIL parses as a time offset. The PDF captures one frame.
  Page CSS colours `<text>`; override with `style="fill:…"`, not `fill=`.
- The page is dark. Use the built-in classes instead of hard-coded colours:
  `box` (with optional `accent`, `ok`, `warn`, `alt`), `edge` (optional `accent`),
  `label`, `muted`, `arrow` (for marker paths). Plain `<text>` is already light.
- Rewriting a commented line detaches its thread (shown as *outdated*, still
  readable). Prefer adding clarifying lines over rewording the commented one.

## Human keys

`j/k` (with counts), `gg/G`, `Ctrl-d/u/f/b`, `[[`/`]]` headings, `h/l w/b/e 0/^/$`, `v`/`V` visual,
`c` comment, `C` whole-document comment, `r` reply, `m/M` next/previous thread,
`Tab` switch to the thread panel, `/` search, `:w` save PDF, `:q` end session,
`?` help; clicking highlighted text opens its thread. Full reference: [README.md](README.md).

## Gotchas

- `--model` must be your real model id; it is the agent's name on the page.
- `wait` returns pending comments until each thread has an agent reply, so a
  second `wait` without replying returns the same feedback immediately.
- The browser URL contains the session token. Keep it local.
