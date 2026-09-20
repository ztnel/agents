#!/usr/bin/env python3
"""Tests for teardown.py — the --merged gate above all else."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"

#: Stand-in for the git-worktree helper, so no real worktree is ever removed.
FAKE_REMOVE = """#!/usr/bin/env python3
import sys, pathlib
pathlib.Path(__file__).with_name("remove.log").write_text(" ".join(sys.argv[1:]))
print("removed")
"""


def make_repo(tmp: str, branch: str = "feat/1-x") -> Path:
    """Init a repo with one commit on *branch*."""
    repo = Path(tmp)
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", branch], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def run_cli(*args: str, home: str | None = None) -> subprocess.CompletedProcess:
    """Invoke teardown.py, optionally with a fake HOME holding a stub helper."""
    env = dict(os.environ)
    if home:
        env["HOME"] = home
    return subprocess.run(
        [sys.executable, str(LIB / "teardown.py"), *args],
        capture_output=True, text=True, env=env,
    )


def fake_home(tmp: str) -> str:
    """Build a HOME whose git-worktree helper is a logging stub."""
    helper = Path(tmp) / ".agents" / "skills" / "git-worktree" / "worktree_remove.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(FAKE_REMOVE, encoding="utf-8")
    helper.chmod(0o755)
    return tmp


class TestMergedGate(unittest.TestCase):
    """Without --merged nothing may be removed. This is the whole point."""

    def test_refuses_without_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            repo = make_repo(tmp)
            result = run_cli("--worktree", str(repo), home=fake_home(home))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("WORKTREE_REMOVED='(skipped: no --merged)'", result.stdout)
            self.assertIn("--merged not given", result.stderr)
            self.assertFalse(
                (Path(home) / ".agents" / "skills" / "git-worktree" / "remove.log").exists(),
                "removal helper must not run without --merged",
            )

    def test_removes_with_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            repo = make_repo(tmp)
            result = run_cli("--worktree", str(repo), "--merged", home=fake_home(home))
            self.assertEqual(result.returncode, 0, result.stderr)
            log = Path(home) / ".agents" / "skills" / "git-worktree" / "remove.log"
            self.assertTrue(log.exists())
            self.assertIn("--force", log.read_text(encoding="utf-8"))
            self.assertIn("--delete-branch", log.read_text(encoding="utf-8"))


class TestBranchResolution(unittest.TestCase):
    """The branch is taken from the checkout unless given explicitly."""

    def test_defaults_to_current_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            repo = make_repo(tmp, branch="feat/42-thing")
            result = run_cli("--worktree", str(repo), home=fake_home(home))
            self.assertIn("LOCAL_BRANCH=feat/42-thing", result.stdout)

    def test_explicit_branch_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            repo = make_repo(tmp)
            result = run_cli(
                "--worktree", str(repo), "--branch", "other/9-x", home=fake_home(home)
            )
            self.assertIn("LOCAL_BRANCH=other/9-x", result.stdout)


class TestRemoteAndUsage(unittest.TestCase):
    """Remote pruning is opt-in; usage errors are distinguishable."""

    def test_remote_skipped_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            repo = make_repo(tmp)
            result = run_cli("--worktree", str(repo), "--merged", home=fake_home(home))
            self.assertIn("REMOTE_DELETED='(skipped: no --prune-remote)'", result.stdout)

    def test_prune_remote_reports_absent_branch(self) -> None:
        """No remote configured must be reported, not crash."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            repo = make_repo(tmp)
            result = run_cli(
                "--worktree", str(repo), "--merged", "--prune-remote",
                home=fake_home(home),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("REMOTE_DELETED='(none:", result.stdout)

    def test_missing_worktree_exits_3(self) -> None:
        self.assertEqual(run_cli("--worktree", "/nonexistent/xyz").returncode, 3)

    def test_worktree_required(self) -> None:
        self.assertEqual(run_cli().returncode, 2)

    def test_missing_helper_exits_3(self) -> None:
        """A HOME with no git-worktree skill is a dependency error, not a crash."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            repo = make_repo(tmp)
            result = run_cli("--worktree", str(repo), "--merged", home=home)
            self.assertEqual(result.returncode, 3, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
