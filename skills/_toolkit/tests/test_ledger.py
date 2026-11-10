#!/usr/bin/env python3
"""Gates of ``ledger.py``: approval matches the live tree, commits are attributed."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "lib" / "ledger.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class LedgerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.env = {**os.environ, "XDG_STATE_HOME": str(root / "state")}
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Ann")
        git(self.repo, "config", "user.email", "ann@example.com")
        (self.repo / "a.py").write_text("a\n")
        git(self.repo, "add", "a.py")
        git(self.repo, "commit", "-qm", "init")
        (self.repo / "a.py").write_text("a2\n")
        (self.repo / "b.py").write_text("b\n")
        (self.repo / "other.txt").write_text("unrelated\n")

    def ledger(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(LEDGER), *args, "--repo", str(self.repo)],
                              capture_output=True, text=True, env=self.env)

    def report(self, **overrides) -> str:
        head = git(self.repo, "rev-parse", "HEAD")
        files = [{"path": p, "state": "reviewed",
                  "content_sha256": hashlib.sha256((self.repo / p).read_bytes()).hexdigest()}
                 for p in ("a.py", "b.py")]
        report = {"verdict": "approved", "approved": True, "head_before": head, "head_after": head,
                  "unanswered": [], "marks": {"files": files}, **overrides}
        path = self.repo.parent / "report.json"
        path.write_text(json.dumps(report))
        return str(path)

    def test_approval_stages_exactly_the_reviewed_files_and_attributes_the_commit(self):
        self.assertEqual(self.ledger("author", "--model", "gen-1", "AUTHORED=a.py").returncode, 0)
        self.ledger("author", "--model", "adv-1", "b.py")
        self.ledger("reviewer", "--model", "rev-1", "a.py")
        approve = self.ledger("approve", "--report", self.report())
        self.assertEqual(approve.returncode, 0, approve.stderr)
        self.assertIn("APPROVER='Ann <ann@example.com>'", approve.stdout)
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only").split(), ["a.py", "b.py"])
        flags = self.ledger("commit-flags")
        self.assertEqual(flags.stdout.split(),
                         ["AUTHOR_MODEL=adv-1", "AUTHOR_MODEL=gen-1", "REVIEWER_MODEL=rev-1"])
        git(self.repo, "commit", "-qm", "x")
        self.assertEqual(self.ledger("committed").returncode, 0)
        self.assertEqual(self.ledger("commit-flags").returncode, 4, "an approval is used once")
        (self.repo / "a.py").write_text("a3\n")
        self.ledger("approve", "--report", self.report(marks={"files": [
            {"path": "a.py", "state": "reviewed",
             "content_sha256": hashlib.sha256(b"a3\n").hexdigest()}]}))
        self.assertIn("no author recorded for: a.py", self.ledger("commit-flags").stderr,
                      "committed attribution does not carry over")

    def test_content_changed_after_review_is_refused_and_nothing_staged(self):
        report = self.report()
        (self.repo / "b.py").write_text("edited after review\n")
        result = self.ledger("approve", "--report", report)
        self.assertEqual(result.returncode, 4)
        self.assertIn("b.py changed since it was reviewed", result.stderr)
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")

    def test_unapproved_unanswered_or_moved_reports_are_refused(self):
        for overrides in ({"verdict": "changes_requested", "approved": False},
                          {"unanswered": [{"id": 1}]},
                          {"head_after": "0" * 40}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.ledger("approve", "--report", self.report(**overrides)).returncode, 4)

    def test_nonempty_index_is_refused(self):
        git(self.repo, "add", "other.txt")
        self.assertEqual(self.ledger("approve", "--report", self.report()).returncode, 4)

    def test_unattributed_or_restaged_index_blocks_commit_flags(self):
        self.ledger("author", "--model", "gen-1", "a.py")
        self.ledger("approve", "--report", self.report())
        missing = self.ledger("commit-flags")
        self.assertEqual(missing.returncode, 4)
        self.assertIn("no author recorded for: b.py", missing.stderr)
        self.ledger("author", "--model", "gen-1", "b.py")
        git(self.repo, "add", "other.txt")
        self.assertIn("changed since approval", self.ledger("commit-flags").stderr)

    def test_placeholder_model_is_rejected(self):
        self.assertEqual(self.ledger("author", "--model", "auto", "a.py").returncode, 2)


if __name__ == "__main__":
    unittest.main()
