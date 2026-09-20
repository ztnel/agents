#!/usr/bin/env python3
"""Executable contract for the draft markdown format: ``lib/issue_draft.py``.

Pins the parse/render/validate API for the H1-title + adaptive-body +
provenance-table draft format:

- H1 title, free-form adaptive body, and a final provenance table.
- Provenance rows: ``Identified by`` (a human email OR a concrete agent model
  ID), ``Authored by`` (all distinct model IDs, order-preserving, never human
  emails), ``Approved by`` (human email only -- never a model ID).
- Placeholder or missing model IDs (``copilot-auto``, ``auto``, ``unknown``,
  blank) are rejected outright -- the format never invents identity.
- Missing or invalid human email for ``Approved by`` is rejected; malformed
  identifiers for ``Identified by`` (neither a valid email nor a concrete
  model ID) are rejected too.
- ``strip_provenance`` yields exactly the title+body that a caller may want
  separately from the provenance ledger; publication behavior (whether the
  ledger is retained on ``issue_write``) is pinned in ``test_issue_review.py``,
  not here.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
MODULE_PATH = LIB / "issue_draft.py"


def _load():
    spec = importlib.util.spec_from_file_location("issue_draft", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load issue_draft from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["issue_draft"] = module
    spec.loader.exec_module(module)
    return module


class IssueDraftFormatTest(unittest.TestCase):
    """Round-trip and validation contract for a single draft document."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def render(self, **overrides):
        kwargs = dict(
            title="Flaky upload retries under load",
            body="## Summary\n\nUploads intermittently 500 under concurrent load.\n",
            identified_by="reporter@example.com",
            authored_by=["gpt-5.1", "claude-sonnet-5"],
            approved_by="approver@example.com",
        )
        kwargs.update(overrides)
        return self.mod.render_draft(**kwargs)

    def test_render_then_parse_round_trips_title_and_provenance(self) -> None:
        markdown = self.render()
        parsed = self.mod.parse_draft(markdown)
        self.assertEqual(parsed.title, "Flaky upload retries under load")
        self.assertIn("Uploads intermittently", parsed.body)
        self.assertEqual(parsed.provenance.identified_by, "reporter@example.com")
        self.assertEqual(
            list(parsed.provenance.authored_by), ["gpt-5.1", "claude-sonnet-5"]
        )
        self.assertEqual(parsed.provenance.approved_by, "approver@example.com")

    def test_distinct_author_model_ids_are_all_retained(self) -> None:
        markdown = self.render(authored_by=["gpt-5.1", "claude-sonnet-5", "gemini-3.6-flash"])
        parsed = self.mod.parse_draft(markdown)
        self.assertEqual(
            list(parsed.provenance.authored_by),
            ["gpt-5.1", "claude-sonnet-5", "gemini-3.6-flash"],
        )

    def test_duplicate_author_model_ids_are_deduplicated_in_order(self) -> None:
        markdown = self.render(authored_by=["gpt-5.1", "claude-sonnet-5", "gpt-5.1"])
        parsed = self.mod.parse_draft(markdown)
        self.assertEqual(list(parsed.provenance.authored_by), ["gpt-5.1", "claude-sonnet-5"])

    def test_parse_requires_h1_title(self) -> None:
        markdown = self.render().replace("# Flaky upload retries under load", "Flaky upload retries under load")
        with self.assertRaises(self.mod.DraftFormatError):
            self.mod.parse_draft(markdown)

    def test_parse_requires_provenance_table(self) -> None:
        markdown = "# Title\n\nJust a body, no provenance section.\n"
        with self.assertRaises(self.mod.DraftFormatError):
            self.mod.parse_draft(markdown)

    def test_parse_requires_all_three_provenance_rows(self) -> None:
        markdown = (
            "# Title\n\nBody.\n\n## Provenance\n\n"
            "| Field | Value |\n|---|---|\n"
            "| Authored by | gpt-5.1 |\n"
        )
        with self.assertRaises(self.mod.DraftFormatError):
            self.mod.parse_draft(markdown)

    def test_rejects_missing_model_id(self) -> None:
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(authored_by=[])

    def test_rejects_blank_model_id(self) -> None:
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(authored_by=["gpt-5.1", "  "])

    def test_rejects_placeholder_model_ids(self) -> None:
        for placeholder in ("copilot-auto", "auto", "unknown", "Unknown", "AUTO"):
            with self.subTest(placeholder=placeholder):
                with self.assertRaises(self.mod.ProvenanceError):
                    self.render(authored_by=["gpt-5.1", placeholder])

    def test_rejects_missing_identified_by_email(self) -> None:
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(identified_by="")

    def test_rejects_invalid_identified_by_email(self) -> None:
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(identified_by="not-an-email")

    def test_rejects_missing_approved_by_email(self) -> None:
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(approved_by="")

    def test_rejects_invalid_approved_by_email(self) -> None:
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(approved_by="not-an-email")

    def test_identified_by_accepts_a_concrete_model_id(self) -> None:
        """``Identified by`` may be either a human email or a concrete agent
        model ID -- e.g. an agent that autonomously spotted the bug and is
        filing the report itself, with no human reporter to email.
        """
        markdown = self.render(identified_by="gpt-5.1")
        parsed = self.mod.parse_draft(markdown)
        self.assertEqual(parsed.provenance.identified_by, "gpt-5.1")

    def test_identified_by_still_accepts_a_human_email(self) -> None:
        markdown = self.render(identified_by="reporter@example.com")
        parsed = self.mod.parse_draft(markdown)
        self.assertEqual(parsed.provenance.identified_by, "reporter@example.com")

    def test_identified_by_rejects_placeholder_model_ids(self) -> None:
        for placeholder in ("copilot-auto", "auto", "unknown", "Unknown", "AUTO"):
            with self.subTest(placeholder=placeholder):
                with self.assertRaises(self.mod.ProvenanceError):
                    self.render(identified_by=placeholder)

    def test_identified_by_rejects_malformed_email_attempt(self) -> None:
        # Contains "@" so it is clearly an attempted email, not a model ID --
        # must still be rejected as a malformed human identifier.
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(identified_by="reporter@")

    def test_identified_by_rejects_blank(self) -> None:
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(identified_by="   ")

    def test_approved_by_rejects_a_concrete_model_id(self) -> None:
        """Unlike ``Identified by``, ``Approved by`` must remain a human email
        only -- a model ID is never an acceptable approver, even a real one.
        """
        with self.assertRaises(self.mod.ProvenanceError):
            self.render(approved_by="gpt-5.1")

    def test_strip_provenance_removes_only_the_provenance_section(self) -> None:
        markdown = self.render()
        stripped = self.mod.strip_provenance(markdown)
        self.assertIn("Flaky upload retries under load", stripped)
        self.assertIn("Uploads intermittently", stripped)
        self.assertNotIn("Provenance", stripped)
        self.assertNotIn("reporter@example.com", stripped)
        self.assertNotIn("gpt-5.1", stripped)

    def test_rendered_provenance_table_has_all_three_labels(self) -> None:
        markdown = self.render()
        for label in ("Identified by", "Authored by", "Approved by"):
            self.assertIn(label, markdown)


if __name__ == "__main__":
    unittest.main(verbosity=2)
