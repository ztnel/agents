#!/usr/bin/env python3
"""Regression coverage for editor environment handoff to tmux."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tuicr_up


class LauncherEnvironmentTest(unittest.TestCase):
    def launch_command(self, environment, *, dirty=True):
        result = SimpleNamespace(ok=True, stdout="%9\n", stderr="")
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {**environment, "TMPDIR": temp_dir}, clear=True))
            run = stack.enter_context(patch.object(tuicr_up, "run", return_value=result))
            stack.enter_context(patch.object(
                tuicr_up, "git",
                return_value=SimpleNamespace(stdout=" M file\n" if dirty else ""),
            ))
            replacements = {
                "resolve_cli_session": "cli-1",
                "already_reviewing": False,
                "resolve_revset": "origin/main...HEAD",
                "active_review_slug": "review-1",
                "start_watch": "watch-1",
                "stop_watch": None,
                "pane_exists": False,
                "review_verdict": {"session": "review-1"},
                "wake_closed_review": None,
                "log_info": None,
            }
            for name, value in replacements.items():
                stack.enter_context(patch.object(tuicr_up, name, return_value=value))
            self.assertEqual(tuicr_up.launch("/repo"), 0)
            return next(
                call.args[0] for call in run.call_args_list
                if call.args[0][:2] == ["tmux", "new-window"]
            )

    def test_editor_values_are_forwarded_as_literal_arguments(self):
        environment = {
            "EDITOR": 'nvim -u "/path with spaces/init.lua"',
            "VISUAL": "code --wait; literal=$value",
        }
        for dirty in (True, False):
            with self.subTest(dirty=dirty):
                args = self.launch_command(environment, dirty=dirty)
                for name, value in environment.items():
                    index = args.index(f"{name}={value}")
                    self.assertEqual(args[index - 1], "-e")
                    self.assertNotIn(value, args[-1])
                command = args[-1].split("; ", 1)[0]
                self.assertNotIn("--stdout", command)
                self.assertNotIn(">", command)
                self.assertIn("tuicr -w" if dirty else "tuicr -r", command)

    def test_unset_variables_leave_tmux_defaults(self):
        self.assertNotIn("-e", self.launch_command({}))

    def test_only_set_variable_is_forwarded(self):
        args = self.launch_command({"EDITOR": "nvim"})
        self.assertEqual(args.count("-e"), 1)
        self.assertIn("EDITOR=nvim", args)

    def test_explicit_empty_value_is_preserved(self):
        args = self.launch_command({"EDITOR": ""})
        self.assertIn("EDITOR=", args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
