#!/usr/bin/env python3
"""Regression coverage for the terminal-independent review launcher."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import warnings
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tuicr_up
from skillkit import workqueue


def queue(task: str) -> tuple[Path, str]:
    """Queue *task* for /repo review-1 in the current state directory's inbox."""
    import sqlite3

    path = workqueue.default_inbox("tuicr-feedback")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(workqueue.SCHEMA)
    key = workqueue.scope("/repo", "review-1")
    workqueue.enqueue(db, key, "human", task)
    db.close()
    return path, key


class LauncherTest(unittest.TestCase):
    def launch(self, *, dirty=True):
        """Run launch() with tuicr and its helpers replaced; return the mocks."""
        with tempfile.TemporaryDirectory() as state, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": state}))
            stale = tuicr_up.report_path("/repo", "review-1")
            stale.write_text("{}", encoding="utf-8")
            inbox, key = queue("applied last round")
            run = stack.enter_context(patch.object(tuicr_up, "run"))
            process = MagicMock()
            process.poll.return_value = 0

            def wait():
                self.assertFalse(stale.exists(), "stale report must be gone while tuicr runs")
                self.assertEqual(workqueue.pending(inbox, key), [], "a new round starts empty")
                return 0

            process.wait.side_effect = wait
            popen = stack.enter_context(patch.object(tuicr_up.subprocess, "Popen", return_value=process))
            status = " M file\n" if dirty else ""
            stack.enter_context(patch.object(
                tuicr_up, "git",
                side_effect=lambda _repo, *args: SimpleNamespace(stdout=status if args[0] == "status" else "abc\n"),
            ))
            mocks = {"run": run, "popen": popen}
            for name, value in {
                "resolve_revset": "origin/main...HEAD",
                "active_review_slug": "review-1",
                "review_verdict": {"session": "review-1", "verdict": "approved"},
                "log_info": None,
            }.items():
                mocks[name] = stack.enter_context(patch.object(tuicr_up, name, return_value=value))
            persist = tuicr_up.persist_close_report
            mocks["wipe_settled"] = stack.enter_context(patch.object(
                tuicr_up, "wipe_settled",
                side_effect=lambda *_: self.assertTrue(mocks["review_verdict"].called) or [],
            ))
            stack.enter_context(patch.object(
                tuicr_up, "persist_close_report",
                side_effect=lambda *args: (
                    self.assertTrue(mocks["wipe_settled"].called, "wipe before the report"),
                    persist(*args),
                )[1],
            ))
            mocks["code"] = tuicr_up.launch("/repo")
            mocks["report"] = json.loads(stale.read_text(encoding="utf-8"))
            return mocks

    def test_close_wipes_settled_comments_between_verdict_and_report(self):
        mocks = self.launch()
        mocks["wipe_settled"].assert_called_once_with("/repo", "review-1")

    def test_settled_keeps_orientation_and_unanswered_comments(self):
        def comment(ident, kind, at, line=1):
            return {"id": ident, "comment_type": kind, "created_at": at,
                    "path": "a.py", "start_line": line}

        comments = [
            comment("desc", "description", "1"),
            comment("note", "review-note", "1", line=2),
            comment("fixed", "issue", "2"),
            comment("answer", "reply", "3"),
            comment("open", "issue", "4"),
            comment("other", "issue", "2", line=9),
            comment("asked", "note", "2", line=5),
            comment("asked-reply", "reply", "3", line=5),
            comment("solved", "note", "2", line=6),
            comment("solution", "issue", "3", line=6),
        ]
        settled = tuicr_up.settled(
            comments, registered={"asked", "solved"}, answered={"solved", "solution"})
        self.assertEqual(settled, ["fixed", "answer", "asked-reply", "solved", "solution"])

    def test_runs_tuicr_in_this_terminal_without_tmux(self):
        for dirty, argv in ((True, ["tuicr", "-w"]), (False, ["tuicr", "-r", "origin/main...HEAD"])):
            with self.subTest(dirty=dirty):
                mocks = self.launch(dirty=dirty)
                mocks["popen"].assert_called_once_with(argv, cwd="/repo")
                self.assertNotIn("stdout", mocks["popen"].call_args.kwargs)
                self.assertFalse(any("tmux" in call.args[0] for call in mocks["run"].call_args_list))

    def test_warns_when_tuicr_hides_unsaved_reviews(self):
        for version, warned in (("tuicr 0.24.0\n", True), ("tuicr 0.28.0\n", False)):
            with self.subTest(version=version), patch.object(
                tuicr_up, "run", return_value=SimpleNamespace(stdout=version),
            ), warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                tuicr_up.warn_outdated()
                self.assertEqual(bool(caught), warned)

    def test_queued_work_blocks_approval(self):
        marks = SimpleNamespace(stdout=json.dumps({"ok": True, "files": []}), stderr="")
        with tempfile.TemporaryDirectory() as state, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": state}))
            stack.enter_context(patch.object(tuicr_up, "run", return_value=marks))
            stack.enter_context(patch.object(tuicr_up.questions, "unanswered", return_value=[]))
            stack.enter_context(patch.object(
                tuicr_up, "git", return_value=SimpleNamespace(ok=True, stdout="abc\n")))
            self.assertEqual(tuicr_up.review_verdict("/repo", "review-1", "abc", 0)["verdict"], "approved")
            queue("rename the helper")
            report = tuicr_up.review_verdict("/repo", "review-1", "abc", 0)
        self.assertEqual((report["verdict"], report["approved"]), ("changes_queued", False))
        self.assertEqual(report["queued"], [{"message_id": "human", "task": "rename the helper"}])

    def test_close_report_replaces_stale_report(self):
        mocks = self.launch()
        self.assertEqual(mocks["code"], 0)
        self.assertEqual(mocks["report"]["verdict"], "approved")

    def test_running_review_of_the_same_checkout_is_not_relaunched(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as repo, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": state}))
            stack.enter_context(patch.object(tuicr_up, "which", return_value="/bin/tuicr"))
            stack.enter_context(patch.object(tuicr_up, "git", return_value=SimpleNamespace(ok=True)))
            launch = stack.enter_context(patch.object(tuicr_up, "launch", return_value=0))
            stack.enter_context(patch.object(tuicr_up, "log_warn"))
            with tuicr_up.review_lock(str(Path(repo).resolve())):
                self.assertEqual(tuicr_up.main([repo]), 0)
            launch.assert_not_called()
            self.assertEqual(tuicr_up.main([repo]), 0)
            launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
