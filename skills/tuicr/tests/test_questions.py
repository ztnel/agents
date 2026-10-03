#!/usr/bin/env python3
"""Explicit question intent and human-answer identity regression tests."""

import copy
import sys
import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tuicr_up
import questions
from skillkit.errors import UsageError


class QuestionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.comments = {
            "q": {"id": "q", "author": "gpt-6.1-sol", "content": "Which repository?",
                  "comment_type": "note", "created_at": "2026-10-03T10:00:00Z"},
            "a": {"id": "a", "author": None, "content": "octo/widgets",
                  "comment_type": "note", "created_at": "2026-10-03T10:01:00Z"},
        }
        for context in (
            patch.object(questions, "_state_path", return_value=Path(self.temp.name) / "questions.json"),
            patch.object(questions, "_comments", side_effect=lambda *_: copy.deepcopy(self.comments)),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_only_explicit_questions_block(self):
        self.assertEqual(questions.unanswered("/repo", "session"), [])
        questions.register("/repo", "session", "q")
        self.assertEqual(len(questions.unanswered("/repo", "session")), 1)
        questions.resolve("/repo", "session", "q", "a")
        self.assertEqual(questions.unanswered("/repo", "session"), [])

    def test_agent_answer_cannot_resolve(self):
        questions.register("/repo", "session", "q")
        self.comments["a"]["author"] = "claude-opus-5.5"
        with self.assertRaises(UsageError):
            questions.resolve("/repo", "session", "q", "a")

    def test_orientation_cannot_be_registered(self):
        for kind in ("description", "review-note"):
            self.comments["q"]["comment_type"] = kind
            with self.assertRaises(UsageError):
                questions.register("/repo", "session", "q")

    def test_resolution_is_invalidated_by_question_or_answer_edits(self):
        for comment_id in ("q", "a"):
            with self.subTest(comment=comment_id):
                original = self.comments[comment_id]["content"]
                questions.register("/repo", "session", "q")
                questions.resolve("/repo", "session", "q", "a")
                self.comments[comment_id]["content"] = "changed"
                self.assertEqual(len(questions.unanswered("/repo", "session")), 1)
                self.comments[comment_id]["content"] = original

    def test_deleted_answer_does_not_resolve(self):
        questions.register("/repo", "session", "q")
        questions.resolve("/repo", "session", "q", "a")
        del self.comments["a"]
        self.assertEqual(len(questions.unanswered("/repo", "session")), 1)

    def test_earlier_human_comment_cannot_resolve(self):
        questions.register("/repo", "session", "q")
        self.comments["a"]["created_at"] = "2026-10-03T09:00:00Z"
        with self.assertRaises(UsageError):
            questions.resolve("/repo", "session", "q", "a")


class PersistedQuestionsTest(unittest.TestCase):
    def test_identity_is_read_from_persisted_comments_at_every_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text(json.dumps({
                "review_comments": [{"id": "review", "author": "gpt-6.1-sol"}],
                "files": {"draft.md": {
                    "file_comments": [{"id": "file", "author": None}],
                    "line_comments": {"4": [{"id": "line", "author": "gpt-6.1-sol"}]},
                }},
            }), encoding="utf-8")
            with patch.object(questions.tuicrio, "session_by_slug",
                          return_value=SimpleNamespace(path=str(path))):
                found = questions._comments("/repo", "session")
            self.assertEqual(set(found), {"review", "file", "line"})
            self.assertIsNone(found["file"]["author"])
            self.assertEqual(found["line"]["author"], "gpt-6.1-sol")


if __name__ == "__main__":
    unittest.main(verbosity=2)
