#!/usr/bin/env python3
"""Regression coverage for the terminal-independent review launcher."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tuicr_up
from skillkit.errors import SkillError


class LauncherTest(unittest.TestCase):
    def launch(self, cli_session, *, dirty=True, wake_error=None):
        """Run launch() with tuicr and its helpers replaced; return the mocks."""
        with tempfile.TemporaryDirectory() as state, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": state}))
            run = stack.enter_context(patch.object(tuicr_up, "run"))
            process = MagicMock()
            process.wait.return_value = 0
            process.poll.return_value = 0
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
                "start_watch": "watch-1",
                "stop_watch": None,
                "review_verdict": {"session": "review-1", "verdict": "approved"},
                "log_info": None,
                "log_warn": None,
            }.items():
                mocks[name] = stack.enter_context(patch.object(tuicr_up, name, return_value=value))
            mocks["wake"] = stack.enter_context(patch.object(
                tuicr_up, "wake_closed_review", side_effect=wake_error,
            ))
            mocks["code"] = tuicr_up.launch("/repo", cli_session)
            mocks["reports"] = list(Path(state).rglob("*.json"))
            return mocks

    def test_runs_tuicr_in_this_terminal_without_tmux(self):
        for dirty, argv in ((True, ["tuicr", "-w"]), (False, ["tuicr", "-r", "origin/main...HEAD"])):
            with self.subTest(dirty=dirty):
                mocks = self.launch("cli-1", dirty=dirty)
                mocks["popen"].assert_called_once_with(argv, cwd="/repo")
                self.assertNotIn("stdout", mocks["popen"].call_args.kwargs)
                self.assertFalse(any("tmux" in call.args[0] for call in mocks["run"].call_args_list))
                mocks["start_watch"].assert_called_once_with("/repo", "review-1", "cli-1")
                mocks["stop_watch"].assert_called_once_with("watch-1")
                mocks["wake"].assert_called_once()

    def test_without_cli_session_persists_report_and_skips_watch_and_wake(self):
        mocks = self.launch("")
        self.assertEqual(mocks["code"], 0)
        mocks["start_watch"].assert_not_called()
        mocks["wake"].assert_not_called()
        self.assertEqual(len(mocks["reports"]), 1)

    def test_wake_failure_keeps_the_close_report(self):
        mocks = self.launch("cli-1", wake_error=SkillError("not inside tmux"))
        self.assertEqual(mocks["code"], 0)
        self.assertEqual(len(mocks["reports"]), 1)
        self.assertIn("close report", mocks["log_warn"].call_args.args[0])

    def test_running_review_of_the_same_checkout_is_not_relaunched(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as repo, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"XDG_STATE_HOME": state}))
            stack.enter_context(patch.object(tuicr_up, "which", return_value="/bin/tuicr"))
            stack.enter_context(patch.object(tuicr_up, "git", return_value=SimpleNamespace(ok=True)))
            launch = stack.enter_context(patch.object(tuicr_up, "launch", return_value=0))
            stack.enter_context(patch.object(tuicr_up, "log_warn"))
            stack.enter_context(patch.object(tuicr_up, "active_review_slug", return_value="review-1"))
            start_watch = stack.enter_context(patch.object(tuicr_up, "start_watch"))
            with tuicr_up.review_lock(str(Path(repo).resolve())):
                self.assertEqual(tuicr_up.main([repo]), 0)
                start_watch.assert_not_called()
                self.assertEqual(tuicr_up.main(["--cli-session", "cli-1", repo]), 0)
                start_watch.assert_called_once_with(str(Path(repo).resolve()), "review-1", "cli-1")
            launch.assert_not_called()
            self.assertEqual(tuicr_up.main([repo]), 0)
            launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
