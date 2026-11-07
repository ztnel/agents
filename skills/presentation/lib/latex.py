"""Convert a common subset of LaTeX math to MathML Core, which browsers render natively.

Covers what explainers usually need: scripts, fractions, roots, Greek letters,
operators and relations, functions, accents, fonts, delimiters, spacing, text,
and matrix-like environments. Unknown commands render as a visible error instead
of failing the page.
"""

from __future__ import annotations

import html
import re

GREEK = {name: chr(code) for name, code in {
    "alpha": 0x3B1, "beta": 0x3B2, "gamma": 0x3B3, "delta": 0x3B4, "epsilon": 0x3F5, "varepsilon": 0x3B5,
    "zeta": 0x3B6, "eta": 0x3B7, "theta": 0x3B8, "vartheta": 0x3D1, "iota": 0x3B9, "kappa": 0x3BA,
    "lambda": 0x3BB, "mu": 0x3BC, "nu": 0x3BD, "xi": 0x3BE, "pi": 0x3C0, "varpi": 0x3D6, "rho": 0x3C1,
    "varrho": 0x3F1, "sigma": 0x3C3, "varsigma": 0x3C2, "tau": 0x3C4, "upsilon": 0x3C5, "phi": 0x3D5,
    "varphi": 0x3C6, "chi": 0x3C7, "psi": 0x3C8, "omega": 0x3C9,
    "Gamma": 0x393, "Delta": 0x394, "Theta": 0x398, "Lambda": 0x39B, "Xi": 0x39E, "Pi": 0x3A0,
    "Sigma": 0x3A3, "Upsilon": 0x3A5, "Phi": 0x3A6, "Psi": 0x3A8, "Omega": 0x3A9,
}.items()}
IDENTIFIERS = {"infty": "∞", "partial": "∂", "nabla": "∇", "hbar": "ℏ", "ell": "ℓ", "emptyset": "∅",
               "varnothing": "∅", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ", "dagger": "†"}
OPERATORS = {
    "pm": "±", "mp": "∓", "times": "×", "div": "÷", "cdot": "⋅", "ast": "∗", "star": "⋆", "circ": "∘",
    "bullet": "∙", "oplus": "⊕", "otimes": "⊗", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠",
    "neq": "≠", "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅", "propto": "∝",
    "ll": "≪", "gg": "≫", "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cup": "∪", "cap": "∩", "wedge": "∧", "land": "∧", "vee": "∨",
    "lor": "∨", "neg": "¬", "lnot": "¬", "forall": "∀", "exists": "∃", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "gets": "←", "leftrightarrow": "↔", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "Leftrightarrow": "⇔", "implies": "⟹", "iff": "⟺", "mapsto": "↦", "uparrow": "↑",
    "downarrow": "↓", "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱",
    "prime": "′", "angle": "∠", "perp": "⊥", "parallel": "∥", "mid": "∣", "colon": ":",
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "vert": "|", "Vert": "‖", "lvert": "|", "rvert": "|", "lVert": "‖", "rVert": "‖",
    "setminus": "∖", "therefore": "∴", "because": "∵", "triangle": "△", "degree": "°",
}
BIG = {"sum": "∑", "prod": "∏", "coprod": "∐", "bigcup": "⋃", "bigcap": "⋂", "int": "∫",
       "iint": "∬", "iiint": "∭", "oint": "∮"}
LIMITS = {"lim", "limsup", "liminf", "max", "min", "sup", "inf", "det", "gcd", "Pr", "argmax", "argmin"}
FUNCTIONS = LIMITS | {"sin", "cos", "tan", "sec", "csc", "cot", "arcsin", "arccos", "arctan", "sinh",
                      "cosh", "tanh", "coth", "log", "ln", "lg", "exp", "dim", "ker", "arg", "deg", "hom"}
ACCENTS = {"hat": "^", "widehat": "^", "bar": "‾", "overline": "‾", "vec": "→", "overrightarrow": "→",
           "dot": "˙", "ddot": "¨", "tilde": "~", "widetilde": "~", "check": "ˇ", "breve": "˘"}
UNDER = {"underline": "_", "underbrace": "⏟"}
SPACES = {",": "0.1667em", ":": "0.2222em", ">": "0.2222em", ";": "0.2778em", " ": "0.25em",
          "quad": "1em", "qquad": "2em", "!": "-0.1667em", "enspace": "0.5em", "thinspace": "0.1667em"}
SIZES = {"big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr"}
STYLES = {"displaystyle": "true", "textstyle": "false"}
FONTS = {"mathbf", "boldsymbol", "bm", "mathbb", "mathcal", "mathscr", "mathfrak", "mathsf", "mathtt",
         "mathit", "mathrm", "mathup"}
TEXTS = {"text", "textrm", "textnormal", "mbox", "textit", "textbf", "texttt", "textsf"}
ENVIRONMENTS = {"matrix": None, "pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"),
                "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"), "smallmatrix": None, "cases": ("{", ""),
                "aligned": None, "align": None, "align*": None, "gathered": None, "array": None, "split": None}
ESCAPED = {"{": "{", "}": "}", "%": "%", "$": "$", "#": "#", "&": "&", "_": "_", "|": "‖", "\\": None}
TOKEN = re.compile(r"\\([A-Za-z]+\*?|.)|(\d+(?:\.\d+)?)|(\s+)|(.)", re.S)


def _alphabet(base_upper: int, base_lower: int | None, digits: int | None = None,
              holes: dict[str, str] | None = None) -> dict[str, str]:
    table = {chr(65 + k): chr(base_upper + k) for k in range(26)}
    if base_lower is not None:
        table.update({chr(97 + k): chr(base_lower + k) for k in range(26)})
    if digits is not None:
        table.update({str(k): chr(digits + k) for k in range(10)})
    table.update(holes or {})
    return table


ALPHABETS = {
    "mathbf": _alphabet(0x1D400, 0x1D41A, 0x1D7CE),
    "mathbb": _alphabet(0x1D538, 0x1D552, 0x1D7D8, {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ",
                                                    "R": "ℝ", "Z": "ℤ"}),
    "mathcal": _alphabet(0x1D49C, 0x1D4B6, None, {"B": "ℬ", "E": "ℰ", "F": "ℱ", "H": "ℋ", "I": "ℐ",
                                                  "L": "ℒ", "M": "ℳ", "R": "ℛ", "e": "ℯ", "g": "ℊ", "o": "ℴ"}),
    "mathfrak": _alphabet(0x1D504, 0x1D51E, None, {"C": "ℭ", "H": "ℌ", "I": "ℑ", "R": "ℜ", "Z": "ℨ"}),
    "mathsf": _alphabet(0x1D5A0, 0x1D5BA, 0x1D7E2),
    "mathtt": _alphabet(0x1D670, 0x1D68A, 0x1D7F6),
}
ALPHABETS["boldsymbol"] = ALPHABETS["bm"] = ALPHABETS["mathbf"]
ALPHABETS["mathscr"] = ALPHABETS["mathcal"]


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _mo(symbol: str, attrs: str = "") -> str:
    return f"<mo{attrs}>{_esc(symbol)}</mo>"


def _row(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"<mrow>{''.join(items)}</mrow>"


class _Parser:
    def __init__(self, source: str) -> None:
        self.tokens = [(m.group(1), m.group(2), m.group(3), m.group(4)) for m in TOKEN.finditer(source)]
        self.pos = 0

    def peek(self) -> tuple | None:
        while self.pos < len(self.tokens) and self.tokens[self.pos][2]:
            self.pos += 1
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple | None:
        token = self.peek()
        self.pos += token is not None
        return token

    def is_char(self, char: str) -> bool:
        token = self.peek()
        return token is not None and token[3] == char

    def is_command(self, *names: str) -> bool:
        token = self.peek()
        return token is not None and token[0] in names

    def raw_group(self) -> str:
        """Source text of the next {group} or single token, for \\text and friends."""
        while self.pos < len(self.tokens) and self.tokens[self.pos][2]:
            self.pos += 1
        if self.pos >= len(self.tokens):
            return ""
        if self.tokens[self.pos][3] != "{":
            command, number, space, char = self.tokens[self.pos]
            self.pos += 1
            return number or char or (space or "") or ("\\" + command if command else "")
        depth, out = 0, []
        while self.pos < len(self.tokens):
            command, number, space, char = self.tokens[self.pos]
            self.pos += 1
            if char == "{":
                depth += 1
                if depth == 1:
                    continue
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            if command is not None:
                out.append(ESCAPED.get(command) or "\\" + command)
            else:
                out.append(number or space or char)
        return "".join(out)

    def optional(self) -> list[str] | None:
        if not self.is_char("["):
            return None
        self.take()
        items = self.sequence(stop="]")
        self.take()
        return items

    def argument(self) -> str:
        token = self.peek()
        if token is None:
            return "<mrow></mrow>"
        if token[3] == "{":
            self.take()
            items = self.sequence(stop="}")
            self.take()
            return _row(items) if items else "<mrow></mrow>"
        return self.atom() or "<mrow></mrow>"

    def sequence(self, stop: str | None = None, cell: bool = False) -> list[str]:
        items: list[str] = []
        while True:
            token = self.peek()
            if token is None:
                return items
            command, _, _, char = token
            if (stop and char == stop) or (cell and (char == "&" or command == "\\")) or command in ("right", "end"):
                return items
            if command == "middle":
                self.take()
                items.append(self.delimiter(stretchy=True))
                continue
            if char == "}":  # stray closing brace
                self.take()
                continue
            item = self.scripted()
            if item:
                items.append(item)

    def scripted(self) -> str:
        token = self.peek()
        name = token[0] if token else None
        base = self.atom()
        if base is None:
            return ""
        sub = sup = None
        while self.is_char("^") or self.is_char("_") or self.is_char("'"):
            if self.is_char("'"):
                self.take()
                primes = "′"
                while self.is_char("'"):
                    self.take()
                    primes += "′"
                sup = (sup or "") + _mo(primes)
                continue
            kind = self.take()[3]
            arg = self.argument()
            if kind == "^":
                sup = arg if sup is None else _row([sup, arg])
            else:
                sub = arg
        space = '<mspace width="0.1667em"></mspace>' if name in FUNCTIONS else ""
        if sub is None and sup is None:
            return base + space
        if name in LIMITS or name in BIG and name not in {"int", "iint", "iiint", "oint"}:
            if sub is not None and sup is not None:
                return f"<munderover>{base}{sub}{sup}</munderover>"
            return (f"<munder>{base}{sub}</munder>" if sub is not None else f"<mover>{base}{sup}</mover>") + space
        if sub is not None and sup is not None:
            return f"<msubsup>{base}{sub}{sup}</msubsup>{space}"
        return (f"<msub>{base}{sub}</msub>" if sub is not None else f"<msup>{base}{sup}</msup>") + space

    def delimiter(self, stretchy: bool) -> str:
        token = self.take()
        if token is None:
            return ""
        command, _, _, char = token
        symbol = char if char is not None else (
            ESCAPED.get(command) if command in ESCAPED else OPERATORS.get(command, "")) or ""
        if symbol == ".":
            symbol = ""
        attrs = ' stretchy="true"' if stretchy else ' stretchy="false"'
        return _mo(symbol, attrs + ' fence="true"') if symbol else "<mo></mo>"

    def atom(self) -> str | None:
        token = self.take()
        if token is None:
            return None
        command, number, _, char = token
        if number is not None:
            return f"<mn>{number}</mn>"
        if char is not None:
            if char == "{":
                items = self.sequence(stop="}")
                self.take()
                return _row(items) if items else "<mrow></mrow>"
            if char.isalpha():
                return f"<mi>{_esc(char)}</mi>"
            if char == "~":
                return '<mspace width="0.25em"></mspace>'
            if char in "&}":
                return None
            symbol = {"-": "−", "*": "∗", "'": "′"}.get(char, char)
            if char in "()[]|":
                return _mo(symbol, ' stretchy="false"')
            return _mo(symbol)
        return self.command(command)

    def command(self, name: str) -> str | None:
        if name in GREEK:
            letter = GREEK[name]
            return f'<mi mathvariant="normal">{letter}</mi>' if name[0].isupper() else f"<mi>{letter}</mi>"
        if name in IDENTIFIERS:
            return f"<mi>{IDENTIFIERS[name]}</mi>"
        if name in OPERATORS:
            return _mo(OPERATORS[name])
        if name in BIG:
            return _mo(BIG[name], ' largeop="true" movablelimits="true"' if name not in {"int", "iint", "iiint", "oint"}
                       else ' largeop="true"')
        if name in FUNCTIONS:
            return f"<mi>{name}</mi>"
        if name in SPACES:
            return f'<mspace width="{SPACES[name]}"></mspace>'
        if name in ESCAPED:
            symbol = ESCAPED[name]
            if symbol is None:
                return None
            return _mo(symbol)
        if name in SIZES:
            return self.delimiter(stretchy=False) if self.peek() else None
        if name in STYLES:
            items = self.sequence(stop="}")
            return f'<mstyle displaystyle="{STYLES[name]}">{"".join(items)}</mstyle>'
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            num, den = self.argument(), self.argument()
            frac = f"<mfrac>{num}{den}</mfrac>"
            return f'<mstyle displaystyle="{str(name != "tfrac").lower()}">{frac}</mstyle>' if name != "frac" else frac
        if name in ("binom", "dbinom", "tbinom"):
            top, bottom = self.argument(), self.argument()
            return (f'<mrow>{_mo("(", " stretchy=\"true\"")}<mfrac linethickness="0">{top}{bottom}</mfrac>'
                    f'{_mo(")", " stretchy=\"true\"")}</mrow>')
        if name == "sqrt":
            index = self.optional()
            body = self.argument()
            return f"<mroot>{body}{_row(index)}</mroot>" if index else f"<msqrt>{body}</msqrt>"
        if name in TEXTS:
            text = self.raw_group()
            # MathML collapses edge whitespace in <mtext>; keep \text{ V} spaced like LaTeX does.
            core = text.strip()
            if core:
                text = "\u00a0" * (text[0].isspace()) + core + "\u00a0" * (text[-1].isspace())
            style = {"textit": ' style="font-style:italic"', "textbf": ' style="font-weight:bold"'}.get(name, "")
            return f"<mtext{style}>{_esc(text)}</mtext>"
        if name == "operatorname" or name == "operatorname*":
            return f"<mi>{_esc(self.raw_group())}</mi>"
        if name in FONTS:
            return self.font(name)
        if name in ACCENTS:
            body = self.argument()
            stretch = ' stretchy="true"' if name.startswith(("wide", "over")) else ""
            return f'<mover accent="true">{body}{_mo(ACCENTS[name], stretch)}</mover>'
        if name in UNDER:
            body = self.argument()
            return f'<munder accentunder="true">{body}{_mo(UNDER[name], " stretchy=\"true\"")}</munder>'
        if name in ("overset", "underset", "stackrel"):
            over, body = self.argument(), self.argument()
            tag = "munder" if name == "underset" else "mover"
            return f"<{tag}>{body}{over}</{tag}>"
        if name == "left":
            opening = self.delimiter(stretchy=True)
            items = self.sequence()
            closing = ""
            if self.is_command("right"):
                self.take()
                closing = self.delimiter(stretchy=True)
            return f"<mrow>{opening}{''.join(items)}{closing}</mrow>"
        if name == "begin":
            return self.environment(self.raw_group())
        if name in ("right", "end"):
            self.raw_group() if name == "end" else self.delimiter(stretchy=True)
            return None
        if name in ("not",):
            target = self.atom() or ""
            return f"<mrow>{target}{_mo(chr(0x338))}</mrow>"
        if name in ("limits", "nolimits", "nonumber", "notag", "label", "tag"):
            if name in ("label", "tag"):
                self.raw_group()
            return None
        return f'<merror><mtext>\\{_esc(name)}</mtext></merror>'

    def font(self, name: str) -> str:
        if name in ("mathrm", "mathup"):
            text = self.raw_group()
            return "".join(f'<mi mathvariant="normal">{_esc(c)}</mi>' if c.isalpha() else
                           (f"<mn>{c}</mn>" if c.isdigit() else _mo(c)) for c in text if not c.isspace())
        if name == "mathit":
            return _row([self.argument()])
        table = ALPHABETS[name]
        text = self.raw_group()
        if "\\" in text or "{" in text:
            return to_mathml_body(text)
        out = [f"<mn>{table.get(c, c)}</mn>" if c.isdigit() else f"<mi>{_esc(table.get(c, c))}</mi>"
               for c in text if not c.isspace()]
        return _row(out) if out else "<mrow></mrow>"

    def environment(self, name: str) -> str:
        if name == "array":
            self.raw_group()
        rows: list[list[str]] = [[]]
        while True:
            items = self.sequence(cell=True)
            rows[-1].append(_row(items) if items else "")
            token = self.take()
            if token is None:
                break
            if token[3] == "&":
                continue
            if token[0] == "\\":
                self.optional()
                rows.append([])
                continue
            if token[0] == "end":
                self.raw_group()
                break
        if rows and rows[-1] == [""]:
            rows.pop()
        align = name in ("aligned", "align", "align*", "split")
        cells = []
        for row in rows:
            tds = []
            for k, cell in enumerate(row):
                side = (' columnalign="right"' if k % 2 == 0 else ' columnalign="left"') if align else (
                    ' columnalign="left"' if name == "cases" else "")
                tds.append(f"<mtd{side}>{cell}</mtd>")
            cells.append(f"<mtr>{''.join(tds)}</mtr>")
        table = f"<mtable>{''.join(cells)}</mtable>"
        if name == "smallmatrix":
            table = f'<mstyle scriptlevel="1">{table}</mstyle>'
        fences = ENVIRONMENTS.get(name)
        if not fences:
            return table
        left, right = fences
        return (f"<mrow>{_mo(left, ' stretchy=\"true\" fence=\"true\"')}{table}"
                f"{_mo(right, ' stretchy=\"true\" fence=\"true\"') if right else ''}</mrow>")


def to_mathml_body(source: str) -> str:
    parser = _Parser(source)
    items: list[str] = []
    while parser.peek() is not None:
        items.extend(parser.sequence())
        if parser.peek() is not None:
            parser.take()
    return _row(items) if items else "<mrow></mrow>"


def to_mathml(source: str, display: bool = False) -> str:
    """Return a ``<math>`` element for the LaTeX *source*."""
    mode = ' display="block"' if display else ""
    return f'<math{mode}>{to_mathml_body(source.strip())}</math>'
