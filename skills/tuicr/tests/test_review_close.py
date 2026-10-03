#!/usr/bin/env python3
"""Tests for clean-close approval classification."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
spec = importlib.util.spec_from_file_location("tuicr_up", LIB / "tuicr_up.py")
tuicr_up = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(tuicr_up)


def result(payload, *, ok=True):
    """Create the process-result shape consumed by `review_verdict`."""
    text = json.dumps(payload) if not isinstance(payload, str) else payload
    return SimpleNamespace(stdout=text, stderr="", ok=ok, returncode=0 if ok else 5)


class ReviewCloseTest(unittest.TestCase):
    """Approval requires clean exit, stable HEAD, and current review marks."""

    def evaluate(self, *, exit_code=0, marks_ok=True, unanswered=None, head="abc"):
        calls = iter([result({"ok": marks_ok}, ok=marks_ok), result(unanswered or [])])
        with patch.object(tuicr_up, "run", side_effect=lambda _args: next(calls)):
            with patch.object(tuicr_up, "git", return_value=result(head)):
                return tuicr_up.review_verdict("/repo", "review-a", "abc", exit_code)

    def test_clean_complete_close_approves(self) -> None:
        report = self.evaluate()
        self.assertTrue(report["approved"])
        self.assertEqual(report["verdict"], "approved")

    def test_killed_or_failed_tuicr_aborts(self) -> None:
        report = self.evaluate(exit_code=None)
        self.assertFalse(report["approved"])
        self.assertEqual(report["verdict"], "aborted")

    def test_unreviewed_files_are_incomplete(self) -> None:
        report = self.evaluate(marks_ok=False)
        self.assertFalse(report["approved"])
        self.assertEqual(report["verdict"], "incomplete")

    def test_unanswered_comments_are_reported_but_do_not_block_approval(self) -> None:
        report = self.evaluate(unanswered=[{"id": "c1"}])
        self.assertTrue(report["approved"])
        self.assertEqual(report["unanswered"], [{"id": "c1"}])
        self.assertNotIn("comments remain unanswered", report["reasons"])

    def test_comment_read_failure_does_not_block_approval(self) -> None:
        calls = iter([result({"ok": True}), result("", ok=False)])
        with patch.object(tuicr_up, "run", side_effect=lambda _args: next(calls)):
            with patch.object(tuicr_up, "git", return_value=result("abc")):
                report = tuicr_up.review_verdict("/repo", "review-a", "abc", 0)
        self.assertTrue(report["approved"])
        self.assertIsNone(report["unanswered"])

    def test_head_change_is_incomplete(self) -> None:
        report = self.evaluate(head="def")
        self.assertFalse(report["approved"])
        self.assertIn("HEAD changed during review", report["reasons"])

    def test_close_token_changes_with_report_state(self) -> None:
        delivered = []

        class Deliverer:
            def __init__(self, *_args):
                pass

            def deliver_prompt(self, prompt, token, _summary):
                delivered.append((prompt, token))
                return True

            def close(self):
                pass

        base = {
            "verdict": "incomplete",
            "head_after": "abc",
            "marks": {"files": [{"path": "a", "state": "unreviewed"}]},
        }
        changed = {
            **base,
            "marks": {"files": [{"path": "a", "state": "reviewed"}]},
        }
        with patch.object(tuicr_up.paths, "state_dir", return_value=Path("/tmp")):
            with patch.object(tuicr_up.paths, "write_json_atomic"):
                with patch.object(tuicr_up, "resolve_target", return_value=(object(), "%1")):
                    with patch.object(tuicr_up, "WakeDeliverer", Deliverer):
                        tuicr_up.wake_closed_review("/repo", "review-a", "cli", base)
                        tuicr_up.wake_closed_review("/repo", "review-a", "cli", changed)
        self.assertNotEqual(delivered[0][1], delivered[1][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
