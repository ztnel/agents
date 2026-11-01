#!/usr/bin/env python3
"""Rendering, sanitising and anchor-relocation contracts for demystify."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import document  # noqa: E402

SOURCE = """# Title

First sentence.
Second **bold** sentence.

```svg
<svg onload="alert(1)"><script>alert(2)</script><g data-label="client"><rect/></g><a href="javascript:x()">x</a></svg>
```

- item one
  continued
  - nested
1. ordered

```py
a = 1
b = 2
```
"""


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.rendered = document.render(SOURCE)
        self.units = {u.line: u for u in self.rendered.units}

    def test_each_source_line_is_a_unit(self):
        self.assertEqual(self.rendered.title, "Title")
        self.assertEqual(self.units[3].text, "First sentence.")
        self.assertEqual(self.units[4].text, "Second bold sentence.")
        self.assertEqual((self.units[10].kind, self.units[10].text), ("item", "item one continued"))
        self.assertEqual(self.units[12].text, "nested")
        self.assertEqual([self.units[16].text, self.units[17].text], ["a = 1", "b = 2"])

    def test_svg_is_one_unit_with_labels_and_no_active_content(self):
        diagram = self.units[6]
        self.assertEqual((diagram.kind, diagram.labels), ("diagram", ["client"]))
        for bad in ("onload", "<script", "alert(2)", "javascript:"):
            self.assertNotIn(bad, self.rendered.html)

    def test_markup_is_escaped(self):
        html = document.render("<img src=x onerror=alert(1)> [x](javascript:alert(1))").html
        self.assertNotIn("<img", html)
        self.assertNotIn("javascript:", html)

    def test_unit_text_keeps_literal_characters(self):
        units = document.render("Use `user_id` and x * y & **z**.\n\n```svg\n<g data-label=\"R&amp;D\"/>\n```").units
        self.assertEqual(units[0].text, "Use user_id and x * y & z.")
        self.assertEqual(units[1].labels, ["R&D"])


    def test_math_renders_as_mathml(self):
        units = document.render("Ohm: $V = IR$, not $5 or \\$x\\$.\n\n$$\n\\frac{a}{b}\n$$\n\n```math\n\\alpha\n```").units
        html = document.render("Ohm: $V = IR$.").html
        self.assertIn("<math><mrow><mi>V</mi><mo>=</mo><mi>I</mi><mi>R</mi></mrow></math>", html)
        self.assertEqual(units[0].text, "Ohm: V=IR, not $5 or $x$.")
        self.assertEqual([(u.line, u.kind, u.text) for u in units[1:]], [(3, "math", "ab"), (7, "math", "α")])

    def test_unknown_latex_is_visible_not_fatal(self):
        html = document.render("$$\\nosuch{x} \\frac{1}$$").html
        self.assertIn("<merror><mtext>\\nosuch</mtext></merror>", html)
        self.assertIn("<mfrac><mn>1</mn><mrow></mrow></mfrac>", html)


class RelocateTest(unittest.TestCase):
    def anchor(self, source, start, end):
        units = [u for u in document.render(source).units if start <= u.line <= end]
        return {"start": start, "end": end, "quote": [u.text for u in units]}

    def test_follows_inserted_lines(self):
        anchor = self.anchor("A.\nB.\nC.", 2, 3)
        moved = document.relocate(anchor, document.render("New.\n\nA.\nB.\nC.").units)
        self.assertEqual((moved["start"], moved["end"], moved["outdated"]), (4, 5, False))

    def test_edited_or_ambiguous_quote_is_outdated_not_guessed(self):
        anchor = self.anchor("A.\nB.", 2, 2)
        self.assertTrue(document.relocate(anchor, document.render("A.\nB changed.").units)["outdated"])
        self.assertTrue(document.relocate(anchor, document.render("B.\n\nB.").units)["outdated"])


if __name__ == "__main__":
    unittest.main()
