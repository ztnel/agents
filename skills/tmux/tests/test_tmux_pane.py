#!/usr/bin/env python3
"""Behaviour tests for tmux_pane.py against a private tmux server."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lib" / "tmux_pane.py"


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class PaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tmux-skill-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.socket = os.path.join(self.tmp, "s")
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "t", "-x", "160", "-y", "50", "sleep 600")
        self.addCleanup(self.tmux, "kill-server")
        self.home = self.tmux("display-message", "-p", "-t", "t", "#{pane_id}")
        # An unrelated pane in another window that nothing may disturb.
        self.bystander = self.tmux("new-window", "-d", "-P", "-F", "#{pane_id}", "-t", "t:", "sleep 600")
        server_pid = self.tmux("display-message", "-p", "#{pid}")
        self.env = {**os.environ, "TMUX": f"{self.socket},{server_pid},0", "TMUX_PANE": self.home}

    def tmux(self, *args):
        result = subprocess.run(["tmux", "-S", self.socket, *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def pane_ids(self):
        return set(self.tmux("list-panes", "-a", "-F", "#{pane_id}").split())

    def call(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, env=env or self.env, timeout=30,
        )

    def ok(self, *args):
        result = self.call(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_open_verifies_expected_process_and_returns_stable_ids(self):
        before = self.pane_ids()
        pane = self.ok("open", "--cwd", self.tmp, "--window", "work", "--expect", "sleep", "--", "sleep", "60")
        self.assertTrue(pane["ready"])
        self.assertRegex(pane["pane_id"], r"^%\d+$")
        self.assertRegex(pane["window_id"], r"^@\d+$")
        self.assertEqual(pane["window_name"], "work")
        self.assertEqual(self.pane_ids(), before | {pane["pane_id"]})
        self.assertEqual(self.tmux("display-message", "-p", "#{pane_id}"), self.home, "open must not steal focus")

    def test_split_targets_only_the_named_pane(self):
        pane = self.ok("open", "--cwd", self.tmp, "--split", "h", "--target", self.bystander, "--", "sleep", "60")
        self.assertEqual(
            self.tmux("display-message", "-p", "-t", pane["pane_id"], "#{window_id}"),
            self.tmux("display-message", "-p", "-t", self.bystander, "#{window_id}"),
        )
        self.assertNotEqual(pane["window_id"], self.tmux("display-message", "-p", "-t", self.home, "#{window_id}"))

    def test_launch_failures_are_explicit(self):
        died = self.call("open", "--cwd", self.tmp, "--", "false")
        self.assertEqual(died.returncode, 5)
        self.assertIn("exited before it became ready", died.stderr)
        missing = self.call("open", "--cwd", self.tmp, "--expect", "never-runs", "--timeout", "0.5", "--", "sleep", "60")
        self.assertEqual(missing.returncode, 5)
        self.assertIn("never-runs", missing.stderr)
        self.assertIn(self.bystander, self.pane_ids())

    def test_environment_is_forwarded_literally(self):
        out = os.path.join(self.tmp, "out")
        env = {**self.env, "EDITOR": 'nvim -u "/a b"; $(touch pwned)'}
        env.pop("UNSET_NAME", None)
        result = self.call(
            "open", "--cwd", self.tmp, "--env", "EDITOR", "--env", "UNSET_NAME", "--",
            "sh", "-c", 'printf %s "$EDITOR" > out; printf %s "${UNSET_NAME-unset}" >> out; sleep 60', env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(out).read_text(), env["EDITOR"] + "unset")
        self.assertFalse(Path(self.tmp, "pwned").exists())

    def test_mutations_require_exact_pane_ids(self):
        for action in ("focus", "capture", "close"):
            result = self.call(action, "--pane", "t:1")
            self.assertEqual(result.returncode, 2, action)
        self.assertEqual(self.call("close", "--pane", "%999").returncode, 4)
        self.assertIn(self.bystander, self.pane_ids())

    def test_close_and_resize_leave_unrelated_panes(self):
        pane = self.ok("open", "--cwd", self.tmp, "--split", "v", "--target", self.home, "--", "sleep", "60")["pane_id"]
        resized = self.ok("resize", "--pane", pane, "--height", "10")
        self.assertEqual(resized["pane_height"], 10)
        self.ok("close", "--pane", pane)
        self.assertEqual(self.pane_ids(), {self.home, self.bystander})

    def test_refuses_to_close_the_calling_pane(self):
        result = self.call("close", "--pane", self.home)
        self.assertEqual(result.returncode, 2)
        self.assertIn(self.home, self.pane_ids())

    def test_focus_selects_window_and_pane(self):
        self.ok("focus", "--pane", self.bystander)
        self.assertEqual(self.tmux("display-message", "-p", "#{pane_id}"), self.bystander)


class UnavailableTest(unittest.TestCase):
    def test_missing_tmux_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as empty:
            env = {**os.environ, "PATH": empty}
            status = subprocess.run([sys.executable, str(SCRIPT), "status"], capture_output=True, text=True, env=env)
            self.assertEqual(status.returncode, 0)
            self.assertFalse(json.loads(status.stdout)["available"])
            opened = subprocess.run(
                [sys.executable, str(SCRIPT), "open", "--cwd", empty, "--", "true"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(opened.returncode, 3)
            self.assertIn("another terminal", opened.stderr)

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_outside_tmux_is_reported(self):
        env = {key: value for key, value in os.environ.items() if key not in ("TMUX", "TMUX_PANE")}
        status = subprocess.run([sys.executable, str(SCRIPT), "status"], capture_output=True, text=True, env=env)
        self.assertFalse(json.loads(status.stdout)["inside"])
        listed = subprocess.run([sys.executable, str(SCRIPT), "list"], capture_output=True, text=True, env=env)
        self.assertEqual(listed.returncode, 3)


if __name__ == "__main__":
    unittest.main()
