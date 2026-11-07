"""Render the agent's Markdown document into navigable, commentable units.

Every source line the human can point at becomes one *unit*: a paragraph line,
heading, list item, table row, quote line, code line, or a whole ``svg`` diagram.
Units carry their 1-based source line, so a comment anchors to the source the
agent edits, and a quote of the unit text lets the anchor follow later revisions.
"""

from __future__ import annotations

import functools
import hashlib
import html
import re
from dataclasses import dataclass, field
from collections.abc import Callable

from latex import to_mathml

WIDGET_LANGS = ("widget", "interactive", "html")
FENCE = re.compile(r"^(```+|~~~+)\s*([\w+-]*)(?:\s+(.*?))?\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")

SVG_DROP = re.compile(r"<(script|iframe|object|embed|style)\b.*?</\1\s*>|<(script|iframe|object|embed)\b[^>]*/?>", re.I | re.S)
SVG_EVENT = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
SVG_JS_URL = re.compile(r"(href\s*=\s*[\"']?)\s*javascript:[^\"'\s>]*", re.I)
SVG_TEXT = re.compile(r"<text\b[^>]*>(.*?)</text\s*>", re.I | re.S)
DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$")
INLINE_MATH = re.compile(r"(?<![\\\w$])\$(?=\S)((?:\\.|[^$\\])+?)(?<=\S)\$(?![\w$])")
LABEL = re.compile(r"data-label\s*=\s*(\"([^\"]*)\"|'([^']*)')")
SOURCE_REF = re.compile(r"\[\^([1-9]\d*)\]")
SOURCE_DEF = re.compile(r"^\[\^([1-9]\d*)\]:\s*(.+)$")


@dataclass
class Unit:
    line: int
    kind: str
    text: str
    labels: list[str] = field(default_factory=list)
    content: str = ""  # selectable text: the unit text, or a diagram's SVG <text> labels
    tex: str = ""  # LaTeX source of a unit containing math, for the agent's comment context

    def __post_init__(self) -> None:
        self.content = self.content or self.text

    def as_dict(self) -> dict:
        data = {"line": self.line, "kind": self.kind, "text": self.text}
        if self.labels:
            data["labels"] = self.labels
        return data


@dataclass
class Rendered:
    title: str
    html: str
    units: list[Unit]
    widgets: dict[str, str] = field(default_factory=dict)  # key -> agent-authored HTML, served sandboxed


def inline(text: str, cite: Callable[[str], str] | None = None) -> str:
    """Escape *text* and apply code, math, strong, emphasis and link spans."""
    codes: list[str] = []

    def keep(markup: str) -> str:
        codes.append(markup)
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = DISPLAY_MATH.sub(lambda m: keep(to_mathml(m.group(1), display=True)), text)
    text = INLINE_MATH.sub(lambda m: keep(to_mathml(m.group(1))), text)
    if cite:
        text = SOURCE_REF.sub(lambda m: keep(cite(m.group(1))), text)
    text = text.replace("\\$", "$")
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\*)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)",
                  lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)

    def link(match: re.Match) -> str:
        label, url = match.group(1), html.unescape(match.group(2)).strip()
        if not re.match(r"^(https?://|#)", url):
            return label
        return f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


@functools.lru_cache(maxsize=1024)
def message_html(text: str) -> str:
    """Render a comment: paragraphs, ``-``/``1.`` lists, fenced code, ``$$`` math and inline spans."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    ordered = False

    def flush() -> None:
        nonlocal items
        if para:
            out.append("<p>" + "<br>".join(inline(line) for line in para) + "</p>")
            para.clear()
        if items:
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            items = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        fence = FENCE.match(stripped)
        if fence:
            flush()
            marker, lang = fence.group(1), fence.group(2).lower()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker[0] * len(marker)):
                body.append(lines[i])
                i += 1
            source = "\n".join(body)
            attrs = f' data-lang="{html.escape(lang)}"' if lang else ""
            out.append(to_mathml(source, display=True) if lang in ("math", "latex", "tex")
                       else f"<pre{attrs}><code>{html.escape(source)}</code></pre>")
        elif stripped.startswith("$$"):
            flush()
            tex = stripped[2:]
            while not tex.rstrip().endswith("$$") and i + 1 < len(lines):
                i += 1
                tex += "\n" + lines[i]
            out.append(to_mathml(tex.strip().removesuffix("$$").strip(), display=True))
        elif not stripped:
            flush()
        elif (item := ITEM.match(line)):
            is_ordered = item.group(2)[0].isdigit()
            if para or (items and is_ordered != ordered):
                flush()
            ordered = is_ordered
            items.append(item.group(3))
        elif items and line[:1].isspace():
            items[-1] += " " + stripped  # continuation of a wrapped item
        else:
            if items:
                flush()
            para.append(stripped)
        i += 1
    flush()
    return "".join(out)


def plain(body_html: str) -> str:
    """Unit text for search and anchor quotes: the rendered text, spaces collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", body_html)).split())


def sanitize_svg(source: str) -> str:
    """Strip active content. The server's CSP is the primary defence; this is hygiene."""
    source = SVG_DROP.sub("", source)
    source = SVG_EVENT.sub("", source)
    return SVG_JS_URL.sub(r"\1#", source)


class _Builder:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.out: list[str] = []
        self.units: list[Unit] = []
        self.widgets: dict[str, str] = {}
        self.sources: dict[str, tuple[int, str]] = {}
        self.citations: set[str] = set()
        self.sources_line = len(lines) + 1

    def cite(self, number: str) -> str:
        self.citations.add(number)
        return (f'<a class="source-ref" href="#source-{number}" data-source="{number}" '
                f'aria-label="Source {number}"></a>')

    def inline(self, text: str) -> str:
        return inline(text, self.cite)

    def bibliography(self) -> None:
        if not self.sources and not self.citations:
            return
        self.out.append(self.unit(self.sources_line, "heading", "Sources", "h2", ' id="sources"'))
        self.out.append('<ol class="sources-list">')
        for number in sorted(self.sources.keys() | self.citations, key=int):
            line, text = self.sources.get(number, (self.sources_line, "**Missing source:** no listing was provided."))
            badge = (f'<span class="source-number" data-source="{number}" '
                     f'aria-label="Source {number}"></span>')
            missing = ' data-missing="true"' if number not in self.sources else ""
            self.out.append(self.unit(line, "source", badge + inline(text), "li",
                                      f' id="source-{number}" data-source-id="{number}"{missing}'))
        self.out.append("</ol>")

    def unit(self, line: int, kind: str, body: str, tag: str = "span", attrs: str = "",
             labels: list[str] | None = None, text: str | None = None, content: str = "",
             tex: str | None = None) -> str:
        index = len(self.units)
        if tex is None:
            tex = self.lines[line - 1].strip() if "<math" in body else ""
        self.units.append(Unit(line, kind, text if text is not None else plain(body), labels or [], content, tex))
        return f'<{tag} class="u u-{kind}" data-u="{index}" data-line="{line}"{attrs}>{body}</{tag}>'


def render(source: str) -> Rendered:
    """Render the supported Markdown subset to HTML plus its ordered unit list."""
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    b = _Builder(lines)
    # Collect definitions outside literal blocks before rendering references.
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        fence = FENCE.match(stripped)
        if fence:
            marker = fence.group(1)
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker[0] * len(marker)):
                i += 1
        elif stripped.startswith("$$"):
            body = stripped[2:]
            while not body.rstrip().endswith("$$") and i + 1 < len(lines):
                i += 1
                body += "\n" + lines[i]
        elif definition := SOURCE_DEF.match(stripped):
            key, text = definition.groups()
            if key in b.sources:
                raise ValueError(f"duplicate source definition [^{key}] on line {i + 1}")
            b.sources[key] = (i + 1, text)
        i += 1
    title = ""
    i = 0
    while i < len(lines):
        raw = lines[i]
        number = i + 1
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue
        definition = SOURCE_DEF.match(stripped)
        if definition:
            i += 1
            continue
        fence = FENCE.match(stripped)
        if fence:
            marker, lang, info = fence.group(1), fence.group(2).lower(), (fence.group(3) or "").strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker[0] * len(marker)):
                body.append(lines[i])
                i += 1
            i += 1
            if lang in ("math", "latex", "tex"):
                tex = "\n".join(body).strip()
                refs = "".join(b.cite(n) for n in SOURCE_REF.findall(info))
                b.out.append(b.unit(number, "math", to_mathml(tex, display=True) + refs, "div", tex=tex))
            elif lang in WIDGET_LANGS:
                source = "\n".join(body)
                key = hashlib.sha256(source.encode()).hexdigest()[:16]
                b.widgets[key] = source
                caption_text = SOURCE_REF.sub("", info).strip()
                name = html.escape(caption_text or "interactive")
                frame = (f'<iframe src="/widget/{key}" sandbox="allow-scripts" title="{name}" '
                         f'referrerpolicy="no-referrer"></iframe>')
                refs = "".join(b.cite(n) for n in SOURCE_REF.findall(info))
                caption = f"<figcaption>{name}{refs}</figcaption>" if info else ""
                b.out.append(b.unit(number, "widget", frame + caption, "figure", text=f"[widget] {caption_text or 'interactive'}"))
            elif lang == "svg":
                svg = sanitize_svg("\n".join(body))
                labels = [html.unescape(m.group(2) if m.group(2) is not None else m.group(3))
                          for m in LABEL.finditer(svg)]
                caption = next((lbl for lbl in labels if lbl), "diagram")
                refs = "".join(b.cite(n) for n in SOURCE_REF.findall(info))
                if refs:
                    svg += f'<figcaption class="diagram-sources">{refs}</figcaption>'
                b.out.append(b.unit(number, "diagram", svg, "figure", labels=labels,
                                    text=f"[diagram] {caption}",
                                    content=" ".join(plain(t) for t in SVG_TEXT.findall(svg))))
            else:
                rows = [b.unit(number + 1 + k, "code", html.escape(text) or " ", text=text)
                        for k, text in enumerate(body)]
                cls = f' data-lang="{html.escape(lang)}"' if lang else ""
                b.out.append(f"<pre{cls}><code>{''.join(rows)}</code></pre>")
            continue
        heading = HEADING.match(stripped)
        if heading:
            level, text = len(heading.group(1)), heading.group(2)
            if level == 2 and text.casefold() == "sources" and (b.sources or b.citations):
                b.sources_line = number
                i += 1
                continue
            if level == 1 and not title:
                title = plain(inline(text))
            b.out.append(b.unit(number, "heading", b.inline(text), f"h{level}"))
            i += 1
            continue
        if stripped.startswith("$$"):
            i = _display_math(lines, i, b)
            continue
        if RULE.match(stripped):
            b.out.append("<hr>")
            i += 1
            continue
        if stripped.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                text = re.sub(r"^\s*>\s?", "", lines[i])
                if text.strip():
                    quote.append(b.unit(i + 1, "quote", b.inline(text)))
                i += 1
            b.out.append(f"<blockquote>{''.join(quote)}</blockquote>")
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            i = _table(lines, i, b)
            continue
        if ITEM.match(raw):
            i = _list(lines, i, b)
            continue
        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not _block_start(lines, i):
            text = lines[i].strip()
            para.append(b.unit(i + 1, "text", b.inline(text)))
            i += 1
        b.out.append(f"<p>{' '.join(para)}</p>")
    b.bibliography()
    return Rendered(title, "\n".join(b.out), b.units, b.widgets)


def _block_start(lines: list[str], i: int) -> bool:
    s = lines[i].strip()
    return bool(FENCE.match(s) or HEADING.match(s) or s.startswith("$$") or RULE.match(s) or s.startswith(">")
                or ITEM.match(lines[i]) or SOURCE_DEF.match(s))


def _display_math(lines: list[str], i: int, b: _Builder) -> int:
    """A ``$$ … $$`` block, on one line or spanning several; the unit is its first line."""
    start, body = i, lines[i].strip()[2:]
    while not body.rstrip().endswith("$$") and i + 1 < len(lines):
        i += 1
        body += "\n" + lines[i]
    tex = body.strip().removesuffix("$$").strip()
    b.out.append(b.unit(start + 1, "math", to_mathml(tex, display=True), "div", tex=tex))
    return i + 1


def _cells(row: str) -> list[str]:
    row = row.strip().strip("|")
    return [cell.strip() for cell in row.split("|")]


def _table(lines: list[str], i: int, b: _Builder) -> int:
    head = _cells(lines[i])
    rows = [b.unit(i + 1, "row", "".join(f"<th>{b.inline(c)}</th>" for c in head), "tr",
                   text=" | ".join(plain(b.inline(c)) for c in head))]
    i += 2
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = _cells(lines[i])
        rows.append(b.unit(i + 1, "row", "".join(f"<td>{b.inline(c)}</td>" for c in cells), "tr",
                           text=" | ".join(plain(b.inline(c)) for c in cells)))
        i += 1
    b.out.append(f"<table>{''.join(rows)}</table>")
    return i


def _list(lines: list[str], i: int, b: _Builder) -> int:
    stack: list[tuple[int, str]] = []
    parts: list[str] = []
    while i < len(lines):
        match = ITEM.match(lines[i])
        if not match:
            break
        indent, marker, text = len(match.group(1).expandtabs(4)), match.group(2), match.group(3)
        tag = "ul" if marker in "-*+" else "ol"
        number = i + 1
        i += 1
        while (i < len(lines) and lines[i].strip() and not ITEM.match(lines[i])
               and not _block_start(lines, i) and lines[i].startswith(" ")):
            text += " " + lines[i].strip()
            i += 1
        while stack and stack[-1][0] > indent:
            parts.append(f"</li></{stack.pop()[1]}>")
        if stack and stack[-1][0] == indent:
            parts.append("</li>")
            if stack[-1][1] != tag:
                parts.append(f"</{stack.pop()[1]}><{tag}>")
                stack.append((indent, tag))
        else:
            parts.append(f"<{tag}>")
            stack.append((indent, tag))
        parts.append("<li>" + b.unit(number, "item", b.inline(text)))
        if i < len(lines) and not lines[i].strip() and i + 1 < len(lines) and ITEM.match(lines[i + 1]):
            i += 1
    while stack:
        parts.append(f"</li></{stack.pop()[1]}>")
    b.out.append("".join(parts))
    return i


def relocate(anchor: dict, units: list[Unit]) -> dict:
    """Move *anchor* to where its quoted units now live, or mark it outdated.

    A unique exact match of the quoted unit sequence wins; ambiguous or missing
    quotes stay readable but detached rather than being guessed into place.
    """
    quote = anchor.get("quote") or []
    if not quote:
        return {**anchor, "outdated": False}
    texts = [unit.text for unit in units]
    hits = [k for k in range(len(texts) - len(quote) + 1) if texts[k:k + len(quote)] == quote]
    if len(hits) != 1:
        return {**anchor, "outdated": True}
    start = hits[0]
    element = anchor.get("element")
    if element and element not in units[start].labels:
        return {**anchor, "outdated": True}
    return {**anchor, "start": units[start].line, "end": units[start + len(quote) - 1].line, "outdated": False}
