import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function client() {
  const elements = new Map();
  const document = { activeElement: null, addEventListener() {}, querySelectorAll: () => [] };
  function element() {
    const classes = new Set();
    return {
      value: "", maxLength: 8000, selectionStart: 0, selectionEnd: 0, dataset: {}, children: [],
      classList: {
        add: (...names) => names.forEach((name) => classes.add(name)),
        remove: (...names) => names.forEach((name) => classes.delete(name)),
        contains: (name) => classes.has(name),
        toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      },
      addEventListener() {}, append() {}, prepend() {}, replaceChildren(...nodes) { this.children = nodes; }, scrollIntoView() {},
      showModal() { this.open = true; }, close() { this.open = false; },
      querySelectorAll: () => [], querySelector: () => null, contains: () => false,
      setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
      focus() { document.activeElement = this; },
      blur() { document.activeElement = null; },
    };
  }
  document.getElementById = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  document.body = element();
  document.createElement = element;
  const storage = { getItem: () => null, setItem() {}, removeItem() {} };
  const copied = [];
  const context = vm.createContext({
    document, sessionStorage: storage, localStorage: storage,
    location: { hash: "" }, addEventListener() {},
    ResizeObserver: class { observe() {} },
    fetch: () => new Promise(() => {}),
    navigator: { clipboard: { async writeText(text) { copied.push(text); } } },
  });
  vm.runInContext(readFileSync(new URL("../assets/app.js", import.meta.url), "utf8"), context);
  const run = (code) => vm.runInContext(code, context);
  run(`state = {threads: [], closed: false}; renderCursor = () => {};`);
  const key = (key) => {
    context.key = key;
    run(`editorKey({key, preventDefault() {}})`);
  };
  function edit(text) {
    context.text = text;
    run(`compose = {key: "test", label: "Test", body: {}}; openComposer();
      $("composer-input").value = text;
      $("composer-input").setSelectionRange(text.length, text.length);`);
  }
  return { run, key, edit, copied, input: document.getElementById("composer-input") };
}

test("viw excludes spaces and vip spans every sentence of the paragraph", () => {
  const { run } = client();
  run(`units = [{classList: {contains: () => false}, dataset: {line: 1}},
    {classList: {contains: () => false}, dataset: {line: 2}}];
    const paragraph = {tagName: "P"};
    units.forEach((u) => {u.parentElement = paragraph;});
    unitCache.set(0, {text: "A first sentence.", nodes: []});
    unitCache.set(1, {text: "Another sentence.", nodes: []});
    cursor = 0; col = 4;
    normalKey({key: "v", preventDefault() {}});
    normalKey({key: "i", preventDefault() {}});
    normalKey({key: "w", preventDefault() {}});`);
  assert.equal(run("selectedText(...Object.values(selection()))"), "first");
  run(`endVisual(); cursor = 1; col = 3;
    normalKey({key: "v", preventDefault() {}});
    normalKey({key: "i", preventDefault() {}});
    normalKey({key: "p", preventDefault() {}});`);
  assert.equal(run("selectedText(...Object.values(selection()))"), "A first sentence. Another sentence.");
  assert.equal(run(`JSON.stringify(innerWord("  café_name!  ", 1))`), "[2,11]");
});

test("Escape keeps the draft open in Normal mode; motions and i use its caret", () => {
  const { run, edit, key, input } = client();
  edit("first word\nsecond line");
  key("Escape");
  assert.equal(run("mode"), "NORMAL");
  assert.equal(run("!!compose"), true);
  assert.equal(input.readOnly, true);
  key("0");
  key("k");
  key("w");
  assert.equal(run("editor.head"), 6);
  key("i");
  assert.equal(input.readOnly, false);
  assert.equal(input.selectionStart, 6);
  assert.equal(input.selectionEnd, 6);
  assert.equal(input.value, "first word\nsecond line");
  assert.equal(run("mode"), "INSERT");
});

test("comment visual text objects and counted line yanks copy without changing the draft", async () => {
  const { run, edit, key, copied, input } = client();
  edit("first word\nsecond line\n\nnext paragraph");
  key("Escape"); key("g"); key("g");
  key("v"); key("i"); key("w"); key("y");
  await new Promise(setImmediate);
  assert.equal(copied.at(-1), "first");
  assert.equal(run("editor.anchor"), null);
  key("2"); key("y"); key("y");
  await new Promise(setImmediate);
  assert.equal(copied.at(-1), "first word\nsecond line\n");
  key("y"); key("i"); key("p");
  await new Promise(setImmediate);
  assert.equal(copied.at(-1), "first word\nsecond line");
  assert.equal(input.value, "first word\nsecond line\n\nnext paragraph");
});

test("closing the panel from either mode keeps a draft and reopening starts Insert", () => {
  const { run, edit, key, input } = client();
  edit("kept");
  key("Escape"); key("Escape");
  assert.equal(run("compose"), null);
  assert.equal(run('drafts.get("test")'), "kept");
  run(`compose = {key: "test", label: "Test", body: {}}; openComposer();`);
  assert.equal(input.value, "kept");
  assert.equal(input.readOnly, false);
  assert.equal(input.classList.contains("normal"), false);
  run("collapsePanel()");
  assert.equal(run('drafts.get("test")'), "kept");
});

test("empty lines and clipboard failures stay explicit and non-destructive", async () => {
  const { run, edit, key, input } = client();
  edit("\nword\n");
  key("Escape"); key("g"); key("g"); key("j");
  assert.equal(run("editor.head"), 1);
  run(`navigator.clipboard.writeText = async () => {throw new Error("denied");}`);
  key("y"); key("i"); key("w");
  await new Promise(setImmediate);
  assert.equal(run('$("message").textContent'), "Could not copy: denied");
  assert.equal(input.value, "\nword\n");
});

test("a delayed clipboard write does not pull the comment back out of Insert mode", async () => {
  const { run, edit, key } = client();
  edit("first word");
  key("Escape"); key("g"); key("g");
  run(`navigator.clipboard.writeText = () => new Promise((resolve) => {globalThis.finishCopy = resolve;});`);
  key("y"); key("i"); key("w");
  key("i");
  run("finishCopy()");
  await new Promise(setImmediate);
  assert.equal(run("mode"), "INSERT");
  assert.equal(run('$("composer-input").readOnly'), false);
});

test("a live thread refresh keeps the composer connected, focused and in Normal mode", () => {
  const { run, edit, key, input } = client();
  edit("keep this draft");
  key("Escape");
  const caret = [input.selectionStart, input.selectionEnd];
  run(`const lookup = document.getElementById;
    const composerNode = $("composer");
    let connected = true, movedOut = false;
    $("threads").contains = (node) => node === composerNode;
    document.body.append = (node) => {if (node === composerNode) movedOut = true;};
    $("threads").replaceChildren = () => {connected = movedOut;};
    document.getElementById = (id) => !connected && id.startsWith("composer") ? null : lookup(id);
    renderThreads();`);
  assert.equal(input.value, "keep this draft");
  assert.deepEqual([input.selectionStart, input.selectionEnd], caret);
  assert.equal(run("mode"), "NORMAL");
  assert.equal(run("document.activeElement === $('composer-input')"), true);
});

test("a inserts after the cursor and A inserts at the current line end", () => {
  const { run, edit, key, input } = client();
  edit("first\nsecond");
  key("Escape"); key("g"); key("g"); key("l"); key("a");
  assert.equal(input.selectionStart, 2);
  assert.equal(input.selectionEnd, 2);
  assert.equal(run("mode"), "INSERT");
  key("Escape"); key("A");
  assert.equal(input.selectionStart, 5);
  assert.equal(run("mode"), "INSERT");
});

test("x deletes characters or a visual selection, but not a line break", async () => {
  const { edit, key, input, copied, run } = client();
  edit("abc\nnext");
  key("Escape"); key("g"); key("g"); key("2"); key("x");
  assert.equal(input.value, "c\nnext");
  assert.equal(copied.at(-1), "ab");
  key("x");
  assert.equal(input.value, "\nnext");
  key("x");
  assert.equal(input.value, "\nnext");
  key("j"); key("v"); key("i"); key("w"); key("x");
  assert.equal(input.value, "\n");
  assert.equal(run("mode"), "NORMAL");
  await new Promise(setImmediate);
});

test("p pastes exactly once even with a count or repeated keys during a clipboard read", async () => {
  const { run, edit, key, input } = client();
  edit("ab\ncd");
  key("Escape"); key("g"); key("g");
  run(`globalThis.reads = 0;
    navigator.clipboard.readText = () => {reads++; return new Promise((resolve) => {globalThis.paste = resolve;});};`);
  key("3"); key("p"); key("p");
  run('paste("XYZ")');
  await new Promise(setImmediate);
  assert.equal(input.value, "aXYZb\ncd");
  assert.equal(run("reads"), 1);
  assert.equal(run("editor.head"), 3);
  assert.equal(run("mode"), "NORMAL");
});

test("paste replaces a selection and rejects stale, denied and oversized clipboard reads", async () => {
  const { run, edit, key, input } = client();
  edit("first word");
  key("Escape"); key("g"); key("g"); key("v"); key("i"); key("w");
  run('navigator.clipboard.readText = async () => "new";');
  key("p");
  await new Promise(setImmediate);
  assert.equal(input.value, "new word");
  run('navigator.clipboard.readText = () => new Promise((resolve) => {globalThis.paste = resolve;});');
  key("p"); key("l"); run('paste("stale")');
  await new Promise(setImmediate);
  assert.equal(input.value, "new word");
  assert.match(run('$("message").textContent'), /Paste cancelled/);
  run('navigator.clipboard.readText = async () => {throw new Error("denied");};');
  key("p");
  await new Promise(setImmediate);
  assert.equal(run('$("message").textContent'), "Could not paste: denied");
  input.maxLength = input.value.length;
  run('navigator.clipboard.readText = async () => "too long";');
  key("p");
  await new Promise(setImmediate);
  assert.equal(input.value, "new word");
  assert.match(run('$("message").textContent'), /exceeds/);
});

test("u undoes deletion, paste and each Insert-mode session in order", async () => {
  const { run, edit, key, input } = client();
  edit("abc");
  key("Escape");
  key("g"); key("g"); key("x");
  assert.equal(input.value, "bc");
  run('navigator.clipboard.readText = async () => "XYZ";');
  key("p");
  await new Promise(setImmediate);
  assert.equal(input.value, "bXYZc");
  key("A");
  input.value += " typed";
  input.setSelectionRange(input.value.length, input.value.length);
  key("Escape"); key("u");
  assert.equal(input.value, "bXYZc");
  assert.equal(run("mode"), "NORMAL");
  key("2"); key("u");
  assert.equal(input.value, "abc");
  assert.equal(run("editor.head"), 0);
  key("u");
  assert.equal(input.value, "");
  key("u");
  assert.equal(run('$("message").textContent'), "nothing to undo");
});

test("undo ignores Insert sessions without edits and restores visual deletions", () => {
  const { edit, key, input } = client();
  edit("first word"); key("Escape");
  key("g"); key("g"); key("v"); key("i"); key("w"); key("x");
  assert.equal(input.value, " word");
  key("i"); key("Escape"); key("u");
  assert.equal(input.value, "first word");
});

test("gd jumps to the cited source; a count selects the nth source on the unit", () => {
  const { run } = client();
  run(`const cited = {classList: {contains: () => false}, dataset: {line: 1}, querySelectorAll: () => [
    {dataset: {source: "2"}}, {dataset: {source: "1"}}]};
    const first = {scrollIntoView() {}};
    const second = {scrollIntoView() {}};
    units = [cited, first, second];
    doc.querySelector = (selector) => selector === "#source-1" ? first : second;
    moveTo = (i) => {cursor = i;};
    prefixedKey = (key) => normalKey({key, preventDefault() {}});
    prefixedKey("g"); prefixedKey("d");`);
  assert.equal(run("cursor"), 2);
  run('cursor = 0; prefixedKey("2"); prefixedKey("g"); prefixedKey("d");');
  assert.equal(run("cursor"), 1);
  run('cursor = 0; units[0].querySelectorAll = () => []; prefix = "g"; prefixed("d");');
  assert.equal(run('$("message").textContent'), "no source attached here");
});

test("Ctrl-o and Ctrl-i restore a source jump's cursor, diagram part and viewport", () => {
  const { run } = client();
  run(`units = Array.from({length: 3}, (_, i) => ({
    dataset: {line: i + 1}, classList: {contains: name => i === 0 && name === "u-diagram"},
    querySelectorAll: () => [], scrollIntoView() {doc.scrollTop = 900;}
  }));
  units.forEach((_, i) => unitCache.set(i, {text: "some source text", nodes: []}));
  doc.querySelector = () => units[2];
  cursor = 0; col = 4; whole = false; element = 1; doc.scrollTop = 120;
  jumpSource("1");
  ctrl = (key) => normalKey({key, ctrlKey: true, preventDefault() {}});
  ctrl("o");`);
  assert.equal(run("cursor"), 0);
  assert.equal(run("col"), 4);
  assert.equal(run("whole"), false);
  assert.equal(run("element"), 1);
  assert.equal(run("doc.scrollTop"), 120);
  run('ctrl("i")');
  assert.equal(run("cursor"), 2);
  assert.equal(run("doc.scrollTop"), 900);
  run('ctrl("o"); whole = false; col = 7; element = -1; jumpSource("1"); ctrl("o");');
  assert.equal(run("col"), 7);
  assert.equal(run("whole"), false);
  assert.equal(run("jumpForward.length"), 1);
  run('whole = true; col = 0; element = 0; jumpSource("1"); ctrl("o");');
  assert.equal(run("whole"), true);
  assert.equal(run("col"), 0);
  assert.equal(run("element"), 0);
});

test("source jump history supports counts, boundaries and branching without recording failed jumps", () => {
  const { run } = client();
  run(`units = Array.from({length: 4}, (_, i) => ({
    dataset: {line: i + 1}, classList: {contains: () => false},
    querySelectorAll: () => [], scrollIntoView() {}
  }));
  units.forEach((_, i) => unitCache.set(i, {text: "source", nodes: []}));
  doc.querySelector = selector => units[Number(selector.slice(-1))];
  ctrl = key => normalKey({key, ctrlKey: true, preventDefault() {}});
  jumpSource("1"); jumpSource("2"); jumpSource("3");
  count = "2"; ctrl("o");`);
  assert.equal(run("cursor"), 1);
  run('ctrl("i")');
  assert.equal(run("cursor"), 2);
  run('jumpSource("1"); ctrl("i");');
  assert.equal(run("cursor"), 1);
  assert.equal(run('$("message").textContent'), "no newer source jump");
  run('jumpSource();');
  assert.equal(run("jumpBack.length"), 3);
  run('count = "99"; ctrl("o"); ctrl("o");');
  assert.equal(run("cursor"), 0);
  assert.equal(run('$("message").textContent'), "no older source jump");
});

test("K inspects all distinct attached sources without moving the cursor or recording a jump", () => {
  const { run } = client();
  run(`units = [{dataset: {}, classList: {contains: () => false},
    querySelectorAll: () => ["1", "2", "1"].map(source => ({dataset: {source}}))}];
    doc.querySelector = selector => ({innerHTML: selector === "#source-1"
      ? '<a href="https://example.com">Reference</a> Notes.' : 'Second reference'});
    cursor = 0; col = 5; doc.scrollTop = 100;
    normalKey({key: "K", preventDefault() {}});`);
  assert.equal(run('$("source-inspector").open'), true);
  assert.equal(run('$("source-inspector-body").children.length'), 2);
  assert.match(run('$("source-inspector-body").children[0].innerHTML'), /Reference<\/a> Notes/);
  assert.equal(run("cursor"), 0);
  assert.equal(run("col"), 5);
  assert.equal(run("doc.scrollTop"), 100);
  assert.equal(run("jumpBack.length"), 0);
  run('units[0].dataset.sourceId = "2"; inspectSources();');
  assert.equal(run('$("source-inspector-body").children.length'), 1);
  assert.equal(run('$("source-inspector-body").children[0].innerHTML'), "Second reference");
});

test("source inspector reports absent references instead of opening an empty window", () => {
  const { run } = client();
  run('units = [{dataset: {}, querySelectorAll: () => []}]; inspectSources();');
  assert.equal(run('$("message").textContent'), "no source attached here");
  assert.notEqual(run('$("source-inspector").open'), true);
  run('units[0].dataset.sourceId = "4"; inspectSources();');
  assert.equal(run('$("message").textContent'), "Source 4 is not listed");
  assert.notEqual(run('$("source-inspector").open'), true);
});
