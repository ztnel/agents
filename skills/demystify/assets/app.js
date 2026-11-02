// demystify client: read-only explainer with vim-style navigation and comments.
// Mode, pane and visual kind are explicit state, never inferred from focus or selection.

const $ = (id) => document.getElementById(id);
const doc = $("doc");

const token = (() => {
  const match = location.hash.match(/t=([\w-]+)/);
  if (match) {
    sessionStorage.setItem("demystify-token", match[1]);
    history.replaceState(null, "", location.pathname);
  }
  return sessionStorage.getItem("demystify-token") || "";
})();

let state = null;
let version = "";
let html = null;
let units = [];
let cursor = 0;
let col = 0;
let wantCol = 0;
let wantX = null; // sticky screen x for j/k, like Vim's curswant
let whole = false; // j/k landed on a diagram: the cursor covers the whole figure
let visual = null;          // null | "line" | "char"
let frozenRows = null;      // a row selection being commented: fixed while the layout reflows
let vstart = null;          // {i, col} where the visual selection began
let element = -1;
let mode = "NORMAL";        // NORMAL | VISUAL | INSERT | COMMAND | SEARCH
let pane = "doc";           // doc | threads
let panelOpen = false;      // floating conversation panel
let tocOpen = localStorage.getItem("demystify-toc") === "1"; // contents sidebar
let count = "";
let prefix = "";
let activeThread = null;
let compose = null;         // {key, label, body}
let searchTerm = "";
let matches = [];
let reachable = true;
const drafts = new Map();
let excerptHits = [];       // [{id, range}] for excerpt threads painted by decorate()
// CSS Custom Highlight API: paints character ranges without touching the DOM.

// -- server ---------------------------------------------------------------
async function api(path, body) {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "X-Token": token, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function poll() {
  for (;;) {
    try {
      const next = await api(`/api/state?since=${version}`);
      reachable = true;
      version = next.version;
      if (!next.unchanged) apply(next);
      else showError(state?.error || "");
    } catch (exc) {
      reachable = false;
      showError(token ? "Server unreachable: the session ended. The page stays readable and :w still exports."
        : "Missing session token: open the URL printed by demystify.py open.");
      renderStatus();
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  }
}

function showError(text) {
  $("error").textContent = text;
  $("error").hidden = !text;
}

// -- text model -------------------------------------------------------------
const lineOf = (i) => Number(units[i]?.dataset.line || 0);
const closed = () => state?.closed || !reachable;
const isDiagram = (i) => units[i]?.classList.contains("u-diagram");
const isWidget = (i) => units[i]?.classList.contains("u-widget");
// Display math and widgets are single stops, like an image: no characters.
const isAtomic = (i) => units[i]?.classList.contains("u-math") || isWidget(i);
const lengthOf = (i) => unitIndex(i).text.length;
const clampCol = (i, c) => Math.max(0, Math.min(c, lengthOf(i) - 1));
const charAt = (i, c) => unitIndex(i).text[c] || "";
const pos = () => ({ i: cursor, col });
const before = (a, b) => a.i < b.i || (a.i === b.i && a.col <= b.col);
const SEP = "\n";           // virtual character between units and between diagram labels
let unitCache = new Map();

function indexForLine(line) {
  let best = 0;
  units.forEach((unit, i) => { if (Number(unit.dataset.line) <= line) best = i; });
  return best;
}

// The characters of one unit: its text nodes, or for a diagram the text inside its
// SVG <text> elements, with a separator between labels so words never merge.
function unitIndex(i) {
  if (unitCache.has(i)) return unitCache.get(i);
  const unit = units[i];
  const nodes = [];
  let text = "";
  if (unit && !isAtomic(i)) {
    const diagram = isDiagram(i);
    const walker = document.createTreeWalker(unit, NodeFilter.SHOW_TEXT, diagram
      ? { acceptNode: (n) => (n.parentElement?.closest("text") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT) }
      : null);
    let label = null;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (diagram) {
        if (!node.data.trim()) continue;
        const owner = node.parentElement.closest("text");
        if (label && owner !== label) text += SEP;
        label = owner;
      }
      nodes.push({ node, start: text.length });
      text += node.data;
    }
  }
  const index = { text, nodes };
  unitCache.set(i, index);
  return index;
}

// Characters of units i0..i1 joined by separators, with each unit's base offset.
function textIndex(i0, i1) {
  const nodes = [];
  const bases = new Map();
  let text = "";
  for (let i = i0; i <= i1; i++) {
    if (i > i0) text += SEP;
    const one = unitIndex(i);
    bases.set(i, text.length);
    for (const rec of one.nodes) nodes.push({ node: rec.node, start: text.length + rec.start, unit: i, unitStart: rec.start });
    text += one.text;
  }
  return { text, nodes, bases };
}

function locate(index, offset) {
  const { nodes } = index;
  let rec = nodes[0];
  for (const item of nodes) { if (item.start <= offset) rec = item; else break; }
  return rec && { node: rec.node, offset: Math.max(0, Math.min(offset - rec.start, rec.node.data.length)),
    unit: rec.unit, col: rec.unitStart + offset - rec.start };
}

function rangeFor(index, from, to) {
  const a = locate(index, from);
  const b = locate(index, to);
  if (!a || !b) return null;
  const range = new Range();
  range.setStart(a.node, a.offset);
  range.setEnd(b.node, b.offset);
  return range;
}

function span(a, b) {
  const index = textIndex(a.i, b.i);
  return { index, from: index.bases.get(a.i) + a.col, to: Math.min(index.bases.get(b.i) + b.col + 1, index.text.length) };
}

// Inclusive character range between two cursor positions.
function charRange(a, b) {
  const { index, from, to } = span(a, b);
  return from < to ? rangeFor(index, from, to) : null;
}

function selectedText(a, b) {
  const { index, from, to } = span(a, b);
  return index.text.slice(from, to).replace(/\s+/g, " ").trim();
}

// The labelled diagram part that owns the character at (i, c), if any.
function partAt(i, c) {
  if (!isDiagram(i)) return null;
  const rec = locate(unitIndex(i), c);
  return rec?.node.parentElement.closest("[data-label]") || null;
}

// Where a thread's excerpt sits now: the whitespace-insensitive match nearest its column hint.
function findExcerpt(anchor) {
  const i0 = indexForLine(anchor.start);
  const i1 = indexForLine(anchor.end);
  const index = textIndex(i0, i1);
  const words = anchor.excerpt.split(/\s+/).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return null;
  let best = null;
  for (const match of index.text.matchAll(new RegExp(words.join("\\s*"), "g"))) {
    if (!best || Math.abs(match.index - (anchor.col || 0)) < Math.abs(best.index - (anchor.col || 0))) best = match;
  }
  if (!best) return null;
  return { range: rangeFor(index, best.index, best.index + best[0].length), start: locate(index, best.index) };
}

// Character marks are boxes drawn over the text from its layout rects. The CSS Custom
// Highlight API would be lighter, but Safari does not reliably paint it.
const LAYERS = ["dm-thread", "dm-thread-pending", "dm-thread-focus", "dm-match", "dm-visual", "dm-cursor"];
const painted = new Map();
let marks = null;

function textRects(range) {
  const root = range.commonAncestorContainer;
  if (root.nodeType === Node.TEXT_NODE) return [...range.getClientRects()].filter((r) => r.width && r.height);
  const rects = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!range.intersectsNode(node)) continue;
    const part = document.createRange();
    part.setStart(node, node === range.startContainer ? range.startOffset : 0);
    part.setEnd(node, node === range.endContainer ? range.endOffset : node.length);
    if (!part.collapsed) rects.push(...[...part.getClientRects()].filter((r) => r.width && r.height));
  }
  return rects;
}

function drawLayer(name) {
  if (!marks) return;
  const layer = marks.querySelector(`[data-layer="${name}"]`);
  const base = doc.getBoundingClientRect();
  const dx = doc.scrollLeft - base.left - doc.clientLeft, dy = doc.scrollTop - base.top - doc.clientTop;
  layer.replaceChildren(...(painted.get(name) || []).flatMap(textRects).map((r) => {
    const box = document.createElement("div");
    box.style.cssText = `left:${r.left + dx}px;top:${r.top + dy}px;width:${r.width}px;height:${r.height}px`;
    return box;
  }));
}

function mountMarks() {
  marks = document.createElement("div");
  marks.id = "marks";
  marks.setAttribute("aria-hidden", "true");
  for (const name of LAYERS) {
    const layer = document.createElement("div");
    layer.dataset.layer = name;
    marks.append(layer);
  }
  doc.append(marks);
}

function paint(name, ranges) {
  painted.set(name, ranges.filter(Boolean));
  drawLayer(name);
}

let relayout = 0;
function redrawMarks() {
  cancelAnimationFrame(relayout);
  relayout = requestAnimationFrame(() => LAYERS.forEach(drawLayer));
}
new ResizeObserver(redrawMarks).observe(doc);
document.fonts?.ready.then(redrawMarks);
doc.addEventListener("transitionend", redrawMarks);

// -- state ----------------------------------------------------------------
// :e reloads the page (fresh assets and document) and lands back on the same spot.
function reloadPage() {
  sessionStorage.setItem("demystify-view", JSON.stringify({ line: lineOf(cursor), col, top: doc.scrollTop }));
  location.reload();
}

function apply(next) {
  const restore = !html && JSON.parse(sessionStorage.getItem("demystify-view") || "null");
  sessionStorage.removeItem("demystify-view");
  const keep = restore || { line: lineOf(cursor), col };
  const keepStart = vstart && { line: lineOf(vstart.i), col: vstart.col };
  state = next;
  if (next.html !== html) {
    html = next.html;
    doc.innerHTML = html;
    mountMarks();
    unitCache = new Map();
    units = [...doc.querySelectorAll(".u")];
    cursor = units.length ? indexForLine(keep.line) : 0;
    col = clampCol(cursor, keep.col);
    if (keepStart) {
      const i = indexForLine(keepStart.line);
      vstart = { i, col: clampCol(i, keepStart.col) };
    }
    element = -1;
    if (searchTerm) runSearch(searchTerm);
    buildToc();
  }
  document.title = next.title || "demystify";
  $("title").textContent = next.title || "demystify";
  $("source").textContent = next.doc;
  showError(next.error);
  renderAgent();
  decorate();
  renderThreads();
  renderCursor(false);
  if (restore) doc.scrollTop = restore.top;
}

function renderAgent() {
  const agent = state.agent;
  const models = agent.models.join(", ");
  $("agent-dot").className = `dot ${agent.state}`;
  $("agent-text").textContent = agent.state === "waiting" ? `${models} · connected, waiting for you`
    : agent.state === "working" ? `${models} · working`
    : models ? `${models} · not connected` : "no agent connected";
}

const isPending = (thread) => thread.messages.at(-1)?.role === "human";
const placed = (thread) => thread.anchor.start > 0 && !thread.anchor.outdated;

function sortedThreads() {
  const rank = (t) => (t.anchor.start === 0 ? -1 : t.anchor.outdated ? 1e9 : t.anchor.start);
  return [...(state?.threads || [])].sort((a, b) => rank(a) - rank(b));
}

function threadsAt(line) {
  return sortedThreads().filter((t) => placed(t) && t.anchor.start <= line && line <= t.anchor.end);
}

function decorate() {
  for (const unit of units) {
    unit.classList.remove("has-thread", "pending", "focus-thread");
    delete unit.dataset.count;
  }
  const counts = new Map();
  const hits = { answered: [], pending: [], focus: [] };
  excerptHits = [];
  for (const thread of (state?.threads || []).filter(placed)) {
    const range = units.filter((u) => thread.anchor.start <= +u.dataset.line && +u.dataset.line <= thread.anchor.end);
    const found = thread.anchor.excerpt ? findExcerpt(thread.anchor) : null;
    if (found) {
      excerptHits.push({ id: thread.id, range: found.range });
      hits[isPending(thread) ? "pending" : "answered"].push(found.range);
      if (thread.id === activeThread) hits.focus.push(found.range);
    }
    for (const unit of range) {
      if (!found) unit.classList.add("has-thread");
      if (isPending(thread)) unit.classList.add("pending");
      if (!found && thread.id === activeThread) unit.classList.add("focus-thread");
    }
    const last = range.at(-1);
    if (last) counts.set(last, (counts.get(last) || 0) + 1);
  }
  for (const [unit, n] of counts) unit.dataset.count = n;
  paint("dm-thread", hits.answered);
  paint("dm-thread-pending", hits.pending);
  paint("dm-thread-focus", hits.focus);
}

// -- conversation panel ---------------------------------------------------
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clip(text, max = 40) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function where(anchor) {
  if (!anchor.start) return "whole document";
  const lines = anchor.start === anchor.end ? `L${anchor.start}` : `L${anchor.start}–${anchor.end}`;
  return (anchor.outdated ? `outdated · was ${lines}` : lines)
    + (anchor.element ? ` · ${anchor.element}` : "")
    + (anchor.excerpt ? ` · “${clip(anchor.excerpt)}”` : "")
    + (anchor.state ? ` · ${clip(Object.entries(anchor.state).map(([k, v]) => `${k}=${v}`).join(", "))}` : "");
}

function renderThreads() {
  const composer = $("composer");
  const input = $("composer-input");
  const focused = document.activeElement === input;
  const caret = [input.selectionStart, input.selectionEnd];
  const list = $("threads");
  list.replaceChildren();
  const threads = sortedThreads();
  for (const thread of threads) {
    const card = el("section", `thread ${isPending(thread) ? "pending" : "answered"}`);
    card.dataset.thread = thread.id;
    if (thread.anchor.outdated) card.classList.add("outdated");
    if (thread.id === activeThread) card.classList.add("active");
    const head = el("div", "where", where({ ...thread.anchor, excerpt: undefined }));
    if (isPending(thread)) head.append(el("span", "badge", "awaiting agent"));
    card.append(head);
    const quoted = thread.anchor.excerpt || thread.anchor.quote?.join("\n");
    if (quoted) card.append(el("div", thread.anchor.excerpt ? "quote excerpt" : "quote", quoted));
    for (const message of thread.messages) {
      const node = el("div", `msg ${message.role}`);
      const when = new Date(message.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      node.append(el("span", "byline", `${message.role === "agent" ? message.model : "you"} · ${when}`));
      const body = el("div", "msg-body");
      body.innerHTML = message.html ?? ""; // rendered and escaped by the server
      if (!message.html) body.textContent = message.content;
      node.append(body);
      card.append(node);
    }
    card.onclick = (event) => {
      if (event.target.closest("#composer")) return;
      activate(thread.id);
      jumpToThread(thread);
    };
    list.append(card);
  }
  $("empty").hidden = threads.length > 0 || !!compose;
  const pending = threads.filter(isPending).length;
  $("pending-count").textContent = pending ? `${pending} awaiting agent` : "";
  const button = $("threads-button");
  button.textContent = `${threads.length} thread${threads.length === 1 ? "" : "s"}${pending ? ` · ${pending} awaiting` : ""}`;
  button.classList.toggle("pending", pending > 0);
  composer.hidden = !compose;
  if (compose) {
    $("composer-target").textContent = compose.label;
    const host = compose.body.thread ? list.querySelector(`[data-thread="${compose.body.thread}"]`) : null;
    if (host) host.append(composer); else list.prepend(composer);
    if (focused) { input.focus({ preventScroll: true }); input.setSelectionRange(...caret); }
  } else {
    document.body.append(composer);
  }
}

// -- contents sidebar ---------------------------------------------------------
function buildToc() {
  const list = $("toc-list");
  list.replaceChildren(...units.flatMap((unit, i) => {
    if (!unit.classList.contains("u-heading")) return [];
    const item = document.createElement("li");
    item.className = unit.tagName.toLowerCase();
    item.textContent = unit.textContent.trim();
    item.dataset.unit = i;
    item.addEventListener("click", () => { pane = "doc"; endVisual(); moveTo(i, 0); unit.scrollIntoView({ block: "start" }); });
    return [item];
  }));
}

function setToc(open) {
  tocOpen = open;
  localStorage.setItem("demystify-toc", open ? "1" : "0");
  $("toc").hidden = !open;
  document.body.classList.toggle("toc-open", open);
  markToc();
}

// Highlight the section holding the cursor.
function markToc() {
  if (!tocOpen) return;
  let current = null;
  for (const item of $("toc-list").children) {
    item.classList.remove("current");
    if (Number(item.dataset.unit) <= cursor) current = item;
  }
  current?.classList.add("current");
  current?.scrollIntoView({ block: "nearest" });
}

function setPanel(open) {
  panelOpen = open;
  $("panel").hidden = !open;
  document.body.classList.toggle("panel-open", open);
  if (!open) pane = "doc";
  units[cursor]?.scrollIntoView({ block: "nearest" });
  renderStatus();
}

function activate(id, scroll = true) {
  activeThread = id;
  if (id && !panelOpen) setPanel(true);
  decorate();
  for (const card of document.querySelectorAll(".thread")) {
    card.classList.toggle("active", card.dataset.thread === id);
    if (scroll && card.dataset.thread === id) card.scrollIntoView({ block: "nearest" });
  }
}

function jumpToThread(thread) {
  if (!placed(thread)) return;
  endVisual();
  const found = thread.anchor.excerpt ? findExcerpt(thread.anchor) : null;
  if (found) moveTo(found.start.unit, found.start.col, true, true);
  else setCursor(indexForLine(thread.anchor.start));
}

// -- cursor ---------------------------------------------------------------
function labelled(i = cursor) {
  return isDiagram(i) ? [...units[i].querySelectorAll("[data-label]")] : [];
}

// A diagram is one stop with its parts cycled by h/l; only search and thread
// jumps (chars) land on a character inside its text.
function setCursor(i, scroll = true, chars = false) {
  if (!units.length) return;
  const next = Math.max(0, Math.min(units.length - 1, i));
  wantX = null;
  if (next !== cursor) element = -1;
  cursor = next;
  whole = isDiagram(cursor) && !chars;
  col = whole ? 0 : clampCol(cursor, wantCol);
  if (charAt(cursor, col) === SEP) col--;
  if (!whole && lengthOf(cursor)) element = labelled().indexOf(partAt(cursor, col));
  renderCursor(scroll);
}

function moveTo(i, c, scroll = true, chars = false) {
  wantCol = c;
  wantX = null;
  setCursor(i, scroll, chars);
}

function endVisual() {
  visual = null;
  frozenRows = null;
  vstart = null;
  if (mode === "VISUAL") mode = "NORMAL";
}

// VISUAL LINE spans whole screen rows; a unit without rows (diagram, display
// math, widget) is selected whole.
function selection() {
  if (visual === "line" && frozenRows) return frozenRows;
  const [a, b] = before(vstart, pos()) ? [vstart, pos()] : [pos(), vstart];
  return visual === "line" ? { a: rowEdge(a, false), b: rowEdge(b, true) } : { a, b };
}

function rowEdge(p, end) {
  const row = rowsOf(p.i).find((r) => r.some((box) => box.i === p.i && box.c === p.col));
  if (!row) return { i: p.i, col: end ? Math.max(0, lengthOf(p.i) - 1) : 0 };
  const box = end ? row.at(-1) : row[0];
  return { i: box.i, col: box.c };
}

const hasRows = (i) => !isDiagram(i) && !isAtomic(i) && lengthOf(i) > 0;

// Character ranges for a row selection, split around units selected whole.
function rowRanges(a, b) {
  const ranges = [];
  let from = null;
  for (let i = a.i; i <= b.i; i++) {
    if (!hasRows(i)) { units[i].classList.add("visual"); continue; }
    from ??= i === a.i ? a : { i, col: 0 };
    if (i === b.i || !hasRows(i + 1)) {
      ranges.push(charRange(from, i === b.i ? b : { i, col: lengthOf(i) - 1 }));
      from = null;
    }
  }
  return ranges.filter(Boolean);
}

function renderCursor(scroll = true) {
  for (const node of doc.querySelectorAll(".cursor, .visual, .element")) node.classList.remove("cursor", "visual", "element", "no-char");
  const unit = units[cursor];
  let selected = [];
  if (unit) {
    unit.classList.add("cursor");
    if (whole || !lengthOf(cursor)) unit.classList.add("no-char");
    if (visual) {
      const { a, b } = selection();
      if (visual === "line") selected = rowRanges(a, b);
      if (visual === "char") selected = [charRange(a, b)];
    }
    labelled()[element]?.classList.add("element");
    if (scroll) unit.scrollIntoView({ block: "nearest" });
  }
  paint("dm-visual", selected);
  paint("dm-cursor", unit && !whole && lengthOf(cursor) ? [charRange(pos(), pos())] : []);
  markToc();
  renderStatus();
}

function renderStatus() {
  const shown = closed() && mode === "NORMAL" ? "CLOSED"
    : mode === "VISUAL" ? (visual === "line" ? "VISUAL LINE" : "VISUAL") : mode;
  $("mode").textContent = shown;
  $("mode").className = { VISUAL: "visual", "VISUAL LINE": "visual", INSERT: "insert", COMMAND: "command", SEARCH: "command", CLOSED: "closed" }[shown] || "";
  $("pane").textContent = pane === "doc" ? "DOC" : "THREADS";
  document.body.classList.toggle("pane-threads", pane === "threads");
  $("keys").textContent = count + prefix;
  const label = labelled()[element]?.dataset.label;
  $("position").textContent = units.length
    ? `Ln ${lineOf(cursor)}:${col + 1}${label ? ` · ${label}` : ""} · ${cursor + 1}/${units.length}` : "";
}

function say(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}

function pageMove(fraction) {
  const rect = units[cursor].getBoundingClientRect();
  const target = rect.top + fraction * doc.clientHeight;
  let best = cursor;
  if (fraction > 0) {
    for (let i = cursor + 1; i < units.length; i++) { best = i; if (units[i].getBoundingClientRect().top >= target) break; }
  } else {
    for (let i = cursor - 1; i >= 0; i--) { best = i; if (units[i].getBoundingClientRect().top <= target) break; }
  }
  doc.scrollBy(0, fraction * doc.clientHeight);
  setCursor(best, false);
  units[cursor].scrollIntoView({ block: "nearest" });
}

function heading(direction, times) {
  let i = cursor;
  for (let n = 0; n < times; n++) {
    let j = i + direction;
    while (j >= 0 && j < units.length && !units[j].classList.contains("u-heading")) j += direction;
    if (j < 0 || j >= units.length) break;
    i = j;
  }
  moveTo(i, 0);
}

// -- character motions ------------------------------------------------------
function words(i) {
  if (isDiagram(i) || !lengthOf(i)) return [{ start: 0, end: 1 }];
  return [...unitIndex(i).text.matchAll(/\w+|[^\w\s]+/g)].map((m) => ({ start: m.index, end: m.index + m[0].length }));
}

// w, b and e cross line boundaries like vim; a diagram counts as one word.
function wordMotion(kind, times) {
  let i = cursor;
  let c = col;
  for (let n = 0; n < times; n++) {
    let found = null;
    for (let j = i; j >= 0 && j < units.length && !found; j += kind === "b" ? -1 : 1) {
      const list = words(j);
      if (kind === "w") found = list.find((w) => j > i || w.start > c);
      else if (kind === "e") found = list.find((w) => j > i || w.end - 1 > c);
      else found = [...list].reverse().find((w) => j < i || w.start < c);
      if (found) { i = j; c = kind === "e" ? found.end - 1 : found.start; }
    }
    if (!found) break;
  }
  moveTo(i, c);
}

// Units laid out together: the sentences of one paragraph flow into shared screen rows.
function blockOf(i) {
  const parent = units[i].parentElement;
  if (parent.tagName !== "P") return [i, i];
  let a = i, b = i;
  while (units[a - 1]?.parentElement === parent) a--;
  while (units[b + 1]?.parentElement === parent) b++;
  return [a, b];
}

function charBoxes(i) {
  const index = unitIndex(i);
  const boxes = [];
  for (let c = 0; c < index.text.length; c++) {
    if (index.text[c] === SEP) continue;
    const rect = [...(rangeFor(index, c, c + 1)?.getClientRects() || [])].find((r) => r.height);
    if (rect) boxes.push({ i, c, x: rect.left + rect.width / 2, top: rect.top, bottom: rect.bottom });
  }
  return boxes;
}

function newRow(box) {
  return Object.assign([box], { bottom: box.bottom });
}

// Screen rows of the block holding unit i, each a list of character boxes.
// Text wraps in reading order; diagrams and display math are single stops.
function rowsOf(i) {
  if (isDiagram(i) || isAtomic(i)) return [];
  const rows = [];
  const [a, b] = blockOf(i);
  for (let u = a; u <= b; u++) {
    for (const box of charBoxes(u)) {
      const row = rows.at(-1);
      // A wrapped row starts fully below the current one; scripts and fractions overlap it.
      if (row && box.top < row.bottom - 2) { row.push(box); row.bottom = Math.max(row.bottom, box.bottom); }
      else rows.push(newRow(box));
    }
  }
  return rows;
}

function vertical(delta) {
  if (!units.length) return;
  const step = Math.sign(delta);
  let i = cursor;
  let rows = rowsOf(i);
  let r = rows.findIndex((row) => row.some((box) => box.i === cursor && box.c === col));
  if (wantX === null && r >= 0) wantX = rows[r].find((box) => box.i === cursor && box.c === col).x;
  for (let n = Math.abs(delta); n > 0; n--) {
    if (r >= 0 && rows[r + step]) { r += step; continue; }
    const [a, b] = rows.length ? [Math.min(...rows.flat().map((x) => x.i)), Math.max(...rows.flat().map((x) => x.i))] : [i, i];
    const next = step > 0 ? Math.max(b, i) + 1 : Math.min(a, i) - 1;
    if (!units[next]) break;
    i = next;
    rows = rowsOf(i);
    r = rows.length ? (step > 0 ? 0 : rows.length - 1) : -1;
  }
  let c = 0;
  if (r >= 0) {
    const target = wantX === null ? rows[r][0]
      : rows[r].reduce((best, box) => (Math.abs(box.x - wantX) < Math.abs(best.x - wantX) ? box : best));
    i = target.i;
    c = target.c;
  }
  if (i !== cursor) element = -1;
  cursor = i;
  wantCol = col = c;
  whole = isDiagram(i);
  element = !whole && lengthOf(cursor) ? labelled().indexOf(partAt(cursor, col)) : -1;
  renderCursor();
}

function horizontal(delta) {
  if (isDiagram(cursor)) { cycleElement(Math.sign(delta)); return; }
  const step = Math.sign(delta);
  const [first, last] = blockOf(cursor);
  let i = cursor;
  let c = col;
  for (let n = Math.abs(delta); n > 0; n--) {
    let next = c + step;
    while (charAt(i, next) === SEP) next += step;
    if (next < 0 || next >= lengthOf(i)) {
      // Sentences of one paragraph flow together, so h/l continue into the neighbour.
      const j = i + step;
      if (j < first || j > last || !lengthOf(j)) break;
      i = j;
      next = step > 0 ? 0 : lengthOf(j) - 1;
    }
    c = next;
  }
  moveTo(i, c, false);
}

function lineEdge(kind) {
  if (isDiagram(cursor)) return;
  const text = unitIndex(cursor).text;
  if (kind === "$") { moveTo(cursor, Infinity, false); return; }
  moveTo(cursor, kind === "^" ? Math.max(0, text.search(/\S/)) : 0, false);
}

// -- comments ---------------------------------------------------------------
function startComment(whole = false) {
  if (closed()) { say("session closed: comments are disabled", true); return; }
  if (whole) {
    compose = { key: "doc", label: "New comment · whole document", body: { document: true } };
  } else {
    if (!units.length) return;
    const { a, b } = visual ? selection() : { a: pos(), b: pos() };
    if (visual === "line") frozenRows = { a, b };
    const body = { start: lineOf(a.i), end: lineOf(b.i) };
    const partial = visual === "line" && hasRows(a.i) && hasRows(b.i)
      && (a.col > 0 || b.col < lengthOf(b.i) - 1);
    if (visual === "char" || partial) {
      body.excerpt = selectedText(a, b);
      body.col = a.col;
      if (!body.excerpt) { say("selection has no text", true); return; }
      const part = partAt(a.i, a.col);
      if (part && a.i === b.i && part === partAt(b.i, b.col)) body.element = part.dataset.label;
    } else if (a.i === b.i && labelled(a.i)[element]) {
      body.element = labelled(a.i)[element].dataset.label;
    }
    const state = a.i === b.i && isWidget(a.i) && widgetState.get(units[a.i].querySelector("iframe"));
    if (state && Object.keys(state).length) body.state = state;
    compose = { key: `new:${body.start}-${body.end}-${body.element || ""}-${body.excerpt || ""}`,
      label: `New comment · ${where(body)}`, body };
  }
  openComposer();
}

function startReply(thread) {
  if (closed()) { say("session closed: comments are disabled", true); return; }
  if (!thread) { say("no conversation here", true); return; }
  activate(thread.id);
  compose = { key: `reply:${thread.id}`, label: `Reply · ${where(thread.anchor)}`, body: { thread: thread.id } };
  openComposer();
}

function openComposer() {
  mode = "INSERT";
  if (!panelOpen) setPanel(true);
  renderThreads();
  const input = $("composer-input");
  input.value = drafts.get(compose.key) || "";
  input.focus();
  $("composer").scrollIntoView({ block: "nearest" });
  renderStatus();
}

function leaveInsert(sent = false) {
  if (compose) {
    const text = $("composer-input").value;
    if (sent || !text.trim()) drafts.delete(compose.key); else drafts.set(compose.key, text);
  }
  compose = null;
  mode = "NORMAL";
  endVisual();
  $("composer-input").blur();
  renderThreads();
  renderCursor(false);
  if (!sent && drafts.size) say("draft kept");
}

async function send() {
  const content = $("composer-input").value.trim();
  if (!content) return;
  try {
    const result = await api("/api/comment", { ...compose.body, content });
    activeThread = result.thread;
    $("composer-input").value = "";
    leaveInsert(true);
    say("sent");
  } catch (exc) {
    say(exc.message, true);
  }
}

// -- search and commands ----------------------------------------------------
function runSearch(term) {
  searchTerm = term;
  for (const unit of units) unit.classList.remove("match");
  matches = [];
  const ranges = [];
  if (term) {
    const sensitive = term !== term.toLowerCase();
    const needle = sensitive ? term : term.toLowerCase();
    units.forEach((unit, i) => {
      const index = textIndex(i, i);
      const text = sensitive ? index.text : index.text.toLowerCase();
      for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + 1)) {
        matches.push({ i, col: at });
        ranges.push(rangeFor(index, at, at + needle.length));
      }
    });
  }
  paint("dm-match", ranges);
}

function jumpMatch(direction) {
  if (!matches.length) { say(searchTerm ? `E486: Pattern not found: ${searchTerm}` : "no search", true); return; }
  const here = pos();
  const after = (m) => m.i > here.i || (m.i === here.i && m.col > here.col);
  const next = direction > 0 ? matches.find(after) ?? matches[0]
    : [...matches].reverse().find((m) => m.i < here.i || (m.i === here.i && m.col < here.col)) ?? matches.at(-1);
  moveTo(next.i, next.col, true, true);
  say(`/${searchTerm} [${matches.indexOf(next) + 1}/${matches.length}]`);
}

function openBar(kind) {
  mode = kind;
  $("command-prefix").textContent = kind === "SEARCH" ? "/" : ":";
  $("command-bar").hidden = false;
  $("command").value = "";
  $("command").focus();
  say("");
  renderStatus();
}

function closeBar() {
  $("command-bar").hidden = true;
  $("command").blur();
  mode = visual ? "VISUAL" : "NORMAL";
  renderStatus();
}

async function closeSession() {
  try {
    await api("/api/close", {});
    say("session closed: conversation saved; reopen to resume");
  } catch (exc) {
    say(exc.message, true);
  }
}

let exporting = null;       // resolves when the print dialog has closed

// Chrome's print() blocks until the dialog closes; Safari's returns at once and opens
// the dialog seconds later. Both fire afterprint when it closes, so wait for that.
function exportPdf() {
  if (exporting) return exporting;
  document.title = state?.title || "demystify";
  $("busy").hidden = false;
  say("rendering PDF…");
  exporting = new Promise((resolve) => {
    let fallback = 0;
    const done = () => {
      clearTimeout(fallback);
      window.removeEventListener("afterprint", done);
      $("busy").hidden = true;
      exporting = null;
      say("PDF dialog closed");
      resolve();
    };
    window.addEventListener("afterprint", done);
    // Never leave the page blocked if a browser skips afterprint; Esc also dismisses.
    fallback = setTimeout(done, 60000);
    $("busy").dismiss = done;
    // Two frames: let the overlay paint before print layout blocks the page.
    requestAnimationFrame(() => requestAnimationFrame(() => window.print()));
  });
  return exporting;
}

function command(text) {
  const cmd = text.trim();
  if (/^\d+$/.test(cmd)) { endVisual(); moveTo(indexForLine(Number(cmd)), 0); return; }
  if (cmd === "w") exportPdf();
  else if (cmd === "e" || cmd === "e!" || cmd === "edit") reloadPage();
  else if (cmd === "q" || cmd === "q!") closeSession();
  else if (cmd === "wq" || cmd === "x") exportPdf().then(closeSession);
  else if (cmd === "toc") setToc(!tocOpen);
  else if (cmd === "noh" || cmd === "nohlsearch") { runSearch(""); say(""); }
  else if (cmd === "h" || cmd === "help") $("help").showModal();
  else if (cmd) say(`E492: Not an editor command: ${cmd}`, true);
}

// -- keys -------------------------------------------------------------------
function takeCount() {
  const n = Math.max(1, parseInt(count || "1", 10));
  count = "";
  return n;
}

function togglePane() {
  if (!panelOpen) setPanel(true);
  pane = pane === "doc" ? "threads" : "doc";
  if (pane === "threads" && !activeThread) activeThread = sortedThreads()[0]?.id || null;
  if (activeThread) activate(activeThread);
  renderStatus();
}

function cycleThread(direction, times = 1) {
  const threads = sortedThreads();
  if (!threads.length) { say("no conversations", true); return; }
  let i = threads.findIndex((t) => t.id === activeThread);
  if (i < 0) {
    const line = lineOf(cursor);
    const start = direction > 0 ? threads.findIndex((t) => placed(t) && t.anchor.start > line)
      : threads.findLastIndex((t) => placed(t) && t.anchor.start < line);
    i = (start < 0 ? (direction > 0 ? 0 : threads.length - 1) : start) - direction;
  }
  i = ((i + direction * times) % threads.length + threads.length) % threads.length;
  activate(threads[i].id);
  if (pane === "doc") jumpToThread(threads[i]);
}

// Vim semantics: V/v toggle their own kind, switch between kinds, and keep the start.
function toggleVisual(kind) {
  if (kind === "char" && whole) kind = "line"; // a whole diagram has no characters to select
  if (visual === kind) { endVisual(); } else {
    vstart = vstart || pos();
    visual = kind;
    mode = "VISUAL";
  }
  renderCursor(false);
}

const DOC_KEYS = {
  j: (n) => vertical(n), ArrowDown: (n) => vertical(n),
  k: (n) => vertical(-n), ArrowUp: (n) => vertical(-n),
  h: (n) => horizontal(-n), ArrowLeft: (n) => horizontal(-n),
  l: (n) => horizontal(n), ArrowRight: (n) => horizontal(n),
  w: (n) => wordMotion("w", n), b: (n) => wordMotion("b", n), e: (n) => wordMotion("e", n),
  0: () => lineEdge("0"), "^": () => lineEdge("^"), $: () => lineEdge("$"),
  G: (n, raw) => moveTo(raw ? indexForLine(n) : units.length - 1, 0),
  "C-d": () => pageMove(0.5), "C-u": () => pageMove(-0.5),
  "C-f": () => pageMove(1), "C-b": () => pageMove(-1),
  v: () => toggleVisual("char"), V: () => toggleVisual("line"),
  o: () => {
    if (!vstart) return;
    const here = pos();
    cursor = vstart.i;
    col = wantCol = vstart.col;
    wantX = null;
    vstart = here;
    renderCursor();
  },
  c: () => startComment(), C: () => startComment(true),
  r: () => startReply(threadsAt(lineOf(cursor)).find((t) => t.id === activeThread) || threadsAt(lineOf(cursor))[0]),
  Enter: () => { if (isWidget(cursor)) { enterWidget(); return; } const here = threadsAt(lineOf(cursor)); if (here.length) activate(here[0].id); else say("no conversation on this line"); },
  m: (n) => cycleThread(1, n), M: (n) => cycleThread(-1, n),
  "{": (n) => heading(-1, n), "}": (n) => heading(1, n),
  n: () => jumpMatch(1), N: () => jumpMatch(-1),
};

const THREAD_KEYS = {
  j: (n) => cycleThread(1, n), ArrowDown: (n) => cycleThread(1, n),
  k: (n) => cycleThread(-1, n), ArrowUp: (n) => cycleThread(-1, n),
  m: (n) => cycleThread(1, n), M: (n) => cycleThread(-1, n),
  G: () => { const t = sortedThreads().at(-1); if (t) activate(t.id); },
  r: () => startReply(state?.threads.find((t) => t.id === activeThread)),
  c: () => startReply(state?.threads.find((t) => t.id === activeThread)),
  Enter: () => { const t = state?.threads.find((x) => x.id === activeThread); if (t) { pane = "doc"; jumpToThread(t); renderStatus(); } },
};

function cycleElement(direction) {
  const parts = labelled();
  whole = true;
  col = 0;
  if (!parts.length) { renderCursor(false); say("this diagram has no labelled parts"); return; }
  element = element < 0 ? (direction > 0 ? 0 : parts.length - 1) : (element + direction + parts.length) % parts.length;
  renderCursor(false);
}

function prefixed(key) {
  const seq = prefix + key;
  prefix = "";
  const n = takeCount();
  if (seq === "gg") { moveTo(0, 0); return; }
  if (seq === "[[") { heading(-1, n); return; }
  if (seq === "]]") { heading(1, n); return; }
  if (seq === "gO") { setToc(!tocOpen); return; }
  if (seq === "zz" || seq === "zt" || seq === "zb") {
    units[cursor]?.scrollIntoView({ block: { zz: "center", zt: "start", zb: "end" }[seq] });
    return;
  }
  renderStatus();
}

function normalKey(event) {
  if (event.metaKey || event.altKey || event.key === "Shift" || event.key === "Control") return;
  const key = event.ctrlKey ? `C-${event.key.toLowerCase()}` : event.key;
  if (event.ctrlKey && !["C-d", "C-u", "C-f", "C-b"].includes(key)) return;
  event.preventDefault();
  if (key === "Escape") {
    count = ""; prefix = "";
    if (visual) endVisual();
    else if (panelOpen) collapsePanel();
    else if (element >= 0) element = -1;
    say("");
    renderCursor(false);
    return;
  }
  if (prefix) { prefixed(key); return; }
  if (/^[0-9]$/.test(key) && (key !== "0" || count)) { count += key; renderStatus(); return; }
  if (["g", "[", "]", "z"].includes(key)) { prefix = key; renderStatus(); return; }
  if (key === "Tab") { count = ""; togglePane(); return; }
  if (key === ":") { count = ""; openBar("COMMAND"); return; }
  if (key === "/") { count = ""; openBar("SEARCH"); return; }
  if (key === "?") { count = ""; $("help").showModal(); return; }
  const raw = count !== "";
  const n = takeCount();
  const action = (pane === "threads" ? THREAD_KEYS : DOC_KEYS)[key];
  const before = cursor;
  if (action) action(n, raw);
  if (cursor !== before && isWidget(cursor)) say("widget · Enter to interact");
  renderStatus();
}

function barKey(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    if (mode === "SEARCH") runSearch(searchTerm);
    closeBar();
  } else if (event.key === "Enter") {
    event.preventDefault();
    const text = $("command").value;
    const kind = mode;
    closeBar();
    if (kind === "SEARCH") { if (text) runSearch(text); jumpMatch(1); } else command(text);
  }
}

function insertKey(event) {
  if (event.key === "Escape") { event.preventDefault(); leaveInsert(); collapsePanel(); }
  else if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); }
}

document.addEventListener("keydown", (event) => {
  if (!$("busy").hidden) {
    event.preventDefault();
    if (event.key === "Escape") $("busy").dismiss?.();
    return;
  }
  if ($("help").open) {
    if (event.key === "Escape" || event.key === "q") { event.preventDefault(); $("help").close(); }
    return;
  }
  if (event.isComposing) return;
  if (mode === "INSERT") insertKey(event);
  else if (mode === "COMMAND" || mode === "SEARCH") barKey(event);
  else normalKey(event);
});

$("command").addEventListener("input", () => { if (mode === "SEARCH") runSearch($("command").value); });
$("composer").addEventListener("submit", (event) => { event.preventDefault(); send(); });
$("composer-input").addEventListener("focus", () => { if (compose && mode !== "INSERT") { mode = "INSERT"; renderStatus(); } });
$("help-button").onclick = () => $("help").showModal();
// -- widgets: sandboxed iframes that talk only through postMessage ------------
const widgetState = new WeakMap(); // iframe -> latest control values, sent with comments

function enterWidget() {
  const frame = units[cursor]?.querySelector("iframe");
  if (!frame) return;
  frame.focus();
  say("widget · Esc returns to the page");
}

function leaveWidget(frame) {
  frame.blur();
  window.focus();
  const i = units.indexOf(frame.closest(".u"));
  if (i >= 0) moveTo(i, 0, false);
  say("");
}

addEventListener("message", (event) => {
  const frame = [...doc.querySelectorAll(".u-widget iframe")].find((f) => f.contentWindow === event.source);
  const data = event.data;
  if (!frame || !data?.demystify) return;
  if (data.type === "height") {
    frame.style.height = `${Math.min(2400, Math.max(40, Number(data.height) || 0))}px`;
    redrawMarks();
  } else if (data.type === "state" && data.state && typeof data.state === "object") {
    widgetState.set(frame, data.state);
  } else if (data.type === "escape") {
    leaveWidget(frame);
  }
});

// A click into a widget moves the page cursor onto it.
addEventListener("blur", () => setTimeout(() => {
  const frame = document.activeElement;
  if (frame?.tagName !== "IFRAME" || !doc.contains(frame)) return;
  const i = units.indexOf(frame.closest(".u"));
  if (i >= 0 && i !== cursor) moveTo(i, 0, false);
  say("widget · Esc returns to the page");
}));

// Esc, × and the threads button all fold the conversation away and return to the page.
function collapsePanel() {
  activeThread = null;
  pane = "doc";
  setPanel(false);
  decorate();
  renderThreads();
  renderCursor(false);
}

$("threads-button").onclick = () => { if (panelOpen) collapsePanel(); else togglePane(); };
$("panel-close").onclick = collapsePanel;
$("toc-close").onclick = () => setToc(false);
setToc(tocOpen);
$("help-close").onclick = () => $("help").close();


function caretAt(x, y) {
  const caret = document.caretPositionFromPoint?.(x, y);
  const range = caret ? null : document.caretRangeFromPoint?.(x, y);
  const node = caret ? caret.offsetNode : range?.startContainer;
  return node ? { node, offset: caret ? caret.offset : range.startOffset } : null;
}

function columnAt(i, point) {
  const rec = point && unitIndex(i).nodes.find((n) => n.node === point.node);
  return rec ? Math.min(rec.start + point.offset, lengthOf(i) - 1) : null;
}

// Threads under the pointer: excerpt threads by their painted range, line threads by their unit.
function threadsAtPoint(unit, point) {
  const ids = [];
  if (point?.node.nodeType === Node.TEXT_NODE) {
    for (const hit of excerptHits) {
      if (hit.range.isPointInRange(point.node, point.offset)) ids.push(hit.id);
    }
  }
  if (unit.classList.contains("has-thread")) {
    const painted = new Set(excerptHits.map((hit) => hit.id));
    for (const thread of threadsAt(Number(unit.dataset.line))) if (!painted.has(thread.id)) ids.push(thread.id);
  }
  return ids;
}

doc.addEventListener("click", (event) => {
  if (mode === "INSERT" || mode === "COMMAND" || mode === "SEARCH") return;
  const unit = event.target.closest(".u");
  if (!unit) return;
  pane = "doc";
  const index = units.indexOf(unit);
  const point = caretAt(event.clientX, event.clientY);
  if (event.shiftKey && !visual) { vstart = pos(); visual = "char"; mode = "VISUAL"; }
  const part = event.target.closest("[data-label]");
  let at = columnAt(index, point);
  // A click on a diagram part's shape lands on that part's first character.
  if (at === null && part) at = unitIndex(index).nodes.find((n) => part.contains(n.node))?.start ?? null;
  moveTo(index, at ?? 0, false);
  if (part && labelled(index).indexOf(part) !== element) { element = labelled(index).indexOf(part); renderCursor(false); }
  if (event.shiftKey || visual) return;
  // Repeated clicks on overlapping threads cycle through them.
  const ids = threadsAtPoint(unit, point);
  if (ids.length) activate(ids[(ids.indexOf(activeThread) + 1) % ids.length]);
});

doc.addEventListener("mousemove", (event) => {
  const unit = event.target.closest(".u");
  doc.classList.toggle("over-thread", !!unit && threadsAtPoint(unit, caretAt(event.clientX, event.clientY)).length > 0);
});

poll();
