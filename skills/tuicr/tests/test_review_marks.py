#!/usr/bin/env python3
"""Tests for the read-only human review-mark gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
REVIEW = SKILL / "lib" / "review.py"


class ReviewMarksTest(unittest.TestCase):
    """Exercise reviewed, unreviewed, absent, and stale current files."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="tuicr-marks."))
        self.repo = self.temp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.com"], check=True)
        self.session = self.temp / "session.json"
        self.comments = self.temp / "comments.json"
        self.comments.write_text("[]\n", encoding="utf-8")
        bindir = self.temp / "bin"
        bindir.mkdir()
        shim = bindir / "tuicr"
        shim.write_text(
            """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:3] == ["review", "list"]:
    print(json.dumps([{"slug": "review-a", "active": False, "comment_count": 0,
                       "path": os.environ["SESSION_FILE"], "updated_at": "now"}]))
elif sys.argv[1:3] == ["review", "comments"]:
    print(open(os.environ["COMMENTS_FILE"], encoding="utf-8").read())
else:
    sys.exit(2)
""",
            encoding="utf-8",
        )
        shim.chmod(0o755)
        self.env = dict(
            os.environ,
            PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
            SESSION_FILE=str(self.session),
            COMMENTS_FILE=str(self.comments),
            PYTHONDONTWRITEBYTECODE="1",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp)

    def write_file(self, name: str, content: str = "x\n") -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_session(self, files: dict[str, bool]) -> None:
        self.session.write_text(
            json.dumps(
                {
                    "version": "1.3",
                    "files": {
                        path: {
                            "path": path,
                            "reviewed": reviewed,
                            "status": "added",
                            "reviewed_hunks": [],
                            "content_hash": 1,
                        }
                        for path, reviewed in files.items()
                    },
                }
            ),
            encoding="utf-8",
        )

    def gate(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(REVIEW),
                "--repo",
                str(self.repo),
                "--session",
                "review-a",
                "reviewed",
                "--gate",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=self.env,
        )

    def test_all_current_files_reviewed_passes(self) -> None:
        self.write_file("a.txt")
        self.write_session({"a.txt": True})
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_unreviewed_file_blocks(self) -> None:
        self.write_file("a.txt")
        self.write_session({"a.txt": False})
        result = self.gate()
        self.assertEqual(result.returncode, 5, result.stdout)
        self.assertEqual(json.loads(result.stdout)["files"][0]["state"], "unreviewed")

    def test_file_absent_from_session_blocks(self) -> None:
        self.write_file("a.txt")
        self.write_session({})
        result = self.gate()
        self.assertEqual(result.returncode, 5, result.stdout)
        self.assertEqual(json.loads(result.stdout)["files"][0]["state"], "absent")

    def test_file_changed_after_save_is_stale(self) -> None:
        self.write_file("a.txt")
        self.write_session({"a.txt": True})
        time.sleep(0.02)
        self.write_file("a.txt", "changed\n")
        result = self.gate()
        self.assertEqual(result.returncode, 5, result.stdout)
        self.assertEqual(json.loads(result.stdout)["files"][0]["state"], "stale")


if __name__ == "__main__":
    unittest.main(verbosity=2)
