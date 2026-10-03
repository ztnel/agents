#!/usr/bin/env python3
"""Tests for selecting tuicr comments that should wake an agent."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[2]
LIB = SKILLS / "tuicr" / "lib"
sys.path.insert(0, str(SKILLS / "_lib"))
sys.path.insert(0, str(LIB))

SPEC = importlib.util.spec_from_file_location("tuicr_watch", LIB / "tuicr_watch.py")
assert SPEC and SPEC.loader
TUICR_WATCH = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TUICR_WATCH
SPEC.loader.exec_module(TUICR_WATCH)

from skillkit.tuicrio import Comment  # noqa: E402


def comment(comment_id: str, comment_type: str) -> Comment:
    """Create one minimal persisted comment."""
    return Comment.from_json(
        {
            "id": comment_id,
            "comment_type": comment_type,
            "content": comment_type,
            "created_at": "2026-01-01T00:00:00Z",
            "path": "src/example.py",
            "start_line": 1,
            "end_line": 1,
            "side": "new",
        }
    )


class WatchSelectionTests(unittest.TestCase):
    def test_registered_questions_do_not_wake_the_agent(self) -> None:
        watcher = TUICR_WATCH.Watcher(TUICR_WATCH.WatchConfig(repo="/repo", session="review"))
        with patch.object(TUICR_WATCH.questions, "registered_ids", return_value={"q"}):
            with patch.object(TUICR_WATCH.tuicrio, "comments", return_value=[
                comment("q", "note"), comment("human-answer", "note"),
            ]):
                self.assertEqual([item.id for item in watcher._read_comments()], ["human-answer"])

    def test_agent_authored_types_do_not_wake_by_default(self) -> None:
        comments = [
            comment("reply", "reply"),
            comment("description", "description"),
            comment("review-note", "review-note"),
            comment("issue", "issue"),
        ]

        selected = TUICR_WATCH.select_unanswered(comments, TUICR_WATCH.WatchConfig())

        self.assertEqual([item.id for item in selected], ["issue"])


if __name__ == "__main__":
    unittest.main()
