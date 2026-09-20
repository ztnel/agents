#!/usr/bin/env python3
"""Unit tests for ``role_guard.py``.

Temp git repos only — no network, no daemon, no fixtures outside ``/tmp``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
sys.path.insert(0, str(LIB))

import role_guard  # noqa: E402

GUARD = LIB / "role_guard.py"


def git(repo: Path, *args: str) -> None:
    """Run a git command in *repo*, failing loudly."""
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class GlobTest(unittest.TestCase):
    """Path-glob semantics."""

    def assert_match(self, pattern: str, path: str, expected: bool) -> None:
        got = role_guard._to_regex(pattern).match(path) is not None
        self.assertEqual(got, expected, f"{pattern!r} vs {path!r}")

    def test_single_star_stays_within_a_segment(self) -> None:
        self.assert_match("tests/*.py", "tests/test_a.py", True)
        self.assert_match("tests/*.py", "tests/deep/test_a.py", False)

    def test_double_star_spans_segments(self) -> None:
        self.assert_match("tests/**", "tests/deep/nested/test_a.py", True)
        self.assert_match("src/**/*.py", "src/a/b/c.py", True)

    def test_double_star_absorbs_its_separator(self) -> None:
        # 'src/**/x.py' should match 'src/x.py' with nothing in between.
        self.assert_match("src/**/x.py", "src/x.py", True)

    def test_trailing_slash_means_everything_under(self) -> None:
        self.assert_match("tests/", "tests/a/b.py", True)
        self.assert_match("tests/", "testsuite/a.py", False)

    def test_unrelated_path_does_not_match(self) -> None:
        self.assert_match("src/**", "tests/test_a.py", False)

    def test_dot_is_literal_not_any_char(self) -> None:
        self.assert_match("a.py", "axpy", False)


class EnforceTest(unittest.TestCase):
    """End-to-end behaviour against a real repository."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "T")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src/app.py").write_text("impl v1\n")
        (self.repo / "tests/test_app.py").write_text("test v1\n")
        (self.repo / "notes.txt").write_text("committed notes\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")
        self.baseline = Path(self.tmp.name) / "baseline.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_guard(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GUARD), *args],
            capture_output=True,
            text=True,
        )

    def snapshot(self) -> None:
        result = self.run_guard("snapshot", "--repo", str(self.repo), "--out", str(self.baseline))
        self.assertEqual(result.returncode, 0, result.stderr)

    def enforce(self, *allow: str) -> subprocess.CompletedProcess[str]:
        args = ["enforce", "--repo", str(self.repo), "--baseline", str(self.baseline)]
        for glob in allow:
            args += ["--allow", glob]
        return self.run_guard(*args)

    def emitted(self, result: subprocess.CompletedProcess[str], key: str) -> set[str]:
        combined = result.stdout + result.stderr
        return {
            line.split("=", 1)[1]
            for line in combined.splitlines()
            if line.startswith(f"{key}=")
        }

    def test_in_scope_change_is_authored_and_kept(self) -> None:
        self.snapshot()
        (self.repo / "src/app.py").write_text("impl v2\n")
        result = self.enforce("src/**")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.emitted(result, "AUTHORED"), {"src/app.py"})
        self.assertEqual((self.repo / "src/app.py").read_text(), "impl v2\n")

    def test_out_of_scope_edit_to_clean_tracked_file_is_reverted(self) -> None:
        self.snapshot()
        (self.repo / "tests/test_app.py").write_text("TAMPERED\n")
        result = self.enforce("src/**")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.emitted(result, "REVERTED"), {"tests/test_app.py"})
        self.assertEqual((self.repo / "tests/test_app.py").read_text(), "test v1\n")

    def test_out_of_scope_new_untracked_file_is_deleted(self) -> None:
        self.snapshot()
        (self.repo / "tests/test_new.py").write_text("sneaky\n")
        result = self.enforce("src/**")
        self.assertEqual(result.returncode, 4)
        self.assertFalse((self.repo / "tests/test_new.py").exists())

    def test_preexisting_dirty_file_is_blocked_never_reverted(self) -> None:
        """The human's uncommitted work must survive a misbehaving sub-agent."""
        (self.repo / "notes.txt").write_text("human work in progress\n")
        self.snapshot()
        (self.repo / "notes.txt").write_text("clobbered by agent\n")
        result = self.enforce("src/**")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.emitted(result, "BLOCKED"), {"notes.txt"})
        # Not restored to HEAD, and not left at the agent's value either --
        # it is handed to the human exactly as found.
        self.assertEqual((self.repo / "notes.txt").read_text(), "clobbered by agent\n")

    def test_untouched_preexisting_dirty_file_is_ignored(self) -> None:
        (self.repo / "notes.txt").write_text("human work in progress\n")
        self.snapshot()
        (self.repo / "src/app.py").write_text("impl v2\n")
        result = self.enforce("src/**")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.emitted(result, "BLOCKED"), set())
        self.assertEqual((self.repo / "notes.txt").read_text(), "human work in progress\n")

    def test_baseline_inside_repo_is_never_judged(self) -> None:
        self.baseline = self.repo / ".guard.json"
        self.snapshot()
        (self.repo / "src/app.py").write_text("impl v2\n")
        result = self.enforce("src/**")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.baseline.exists(), "guard deleted its own baseline")

    def test_deleting_someone_elses_file_is_reverted(self) -> None:
        self.snapshot()
        (self.repo / "tests/test_app.py").unlink()
        result = self.enforce("src/**")
        self.assertEqual(result.returncode, 4)
        self.assertEqual((self.repo / "tests/test_app.py").read_text(), "test v1\n")

    def test_dry_run_reports_without_reverting(self) -> None:
        self.snapshot()
        (self.repo / "tests/test_app.py").write_text("TAMPERED\n")
        args = [
            "enforce",
            "--repo",
            str(self.repo),
            "--baseline",
            str(self.baseline),
            "--allow",
            "src/**",
            "--dry-run",
        ]
        result = self.run_guard(*args)
        self.assertEqual(result.returncode, 4)
        self.assertEqual((self.repo / "tests/test_app.py").read_text(), "TAMPERED\n")

    def test_two_roles_cannot_cross(self) -> None:
        """The adversary owns tests, the generator owns src."""
        self.snapshot()
        (self.repo / "tests/test_app.py").write_text("test v2\n")
        (self.repo / "src/app.py").write_text("impl by adversary\n")
        result = self.enforce("tests/**")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(self.emitted(result, "AUTHORED"), {"tests/test_app.py"})
        self.assertEqual((self.repo / "src/app.py").read_text(), "impl v1\n")

    def test_enforce_requires_an_allow_glob(self) -> None:
        self.snapshot()
        result = self.run_guard(
            "enforce", "--repo", str(self.repo), "--baseline", str(self.baseline)
        )
        self.assertNotEqual(result.returncode, 0)

    def test_missing_repo_is_reported(self) -> None:
        result = self.run_guard(
            "snapshot", "--repo", str(self.repo / "nope"), "--out", str(self.baseline)
        )
        self.assertNotEqual(result.returncode, 0)

    def test_snapshot_records_digests(self) -> None:
        (self.repo / "notes.txt").write_text("dirty\n")
        self.snapshot()
        state = json.loads(self.baseline.read_text())
        self.assertIn("notes.txt", state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
