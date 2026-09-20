#!/usr/bin/env python3
"""Executable contract for the persistent draft workspace: ``lib/issue_workspace.py``.

Pins:

- Default root ``~/.local/state/agents/github-issue``, overridable for tests
  via ``$XDG_STATE_HOME`` (the same override ``skillkit.paths`` already
  honours -- no bespoke env var is invented).
- The workspace is its own git repository, entirely separate from whatever
  repository the issue targets: creating/saving a draft must never dirty the
  target checkout.
- The workspace has a valid baseline commit (a resolvable ``HEAD``) so tuicr
  has something to diff the draft against.
- A draft's markdown and target metadata are both persisted, and both remain
  **working-tree changes** -- ``save_draft``/updates must never auto-commit
  ``draft.md``/``metadata.json``. tuicr reviews the unstaged diff; an
  auto-commit would leave nothing for a human to review or for a close
  report to cover.
- The same draft identifier reuses the same workspace and its git history,
  rather than creating a fresh one each time.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

import os
import tempfile

LIB = Path(__file__).resolve().parents[1] / "lib"
MODULE_PATH = LIB / "issue_workspace.py"


def _load():
    spec = importlib.util.spec_from_file_location("issue_workspace", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load issue_workspace from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["issue_workspace"] = module
    spec.loader.exec_module(module)
    return module


class IssueWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_home = Path(self._tmp.name) / "state"
        self.target_repo = Path(self._tmp.name) / "target-repo"
        self.target_repo.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet"], cwd=self.target_repo, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.target_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=self.target_repo, check=True
        )
        (self.target_repo / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.target_repo, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "init"], cwd=self.target_repo, check=True
        )

        self._env_patch = {
            "XDG_STATE_HOME": str(self.state_home),
        }
        self._old_env = {k: os.environ.get(k) for k in self._env_patch}
        os.environ.update(self._env_patch)
        self.addCleanup(self._restore_env)

        self.mod = _load()

    def _restore_env(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _target_repo_is_clean(self) -> bool:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.target_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == ""

    def test_default_workspace_root_is_under_xdg_state_home(self) -> None:
        root = self.mod.workspace_root()
        self.assertEqual(root, self.state_home / "agents" / "github-issue")

    def test_ensure_workspace_creates_a_git_repository(self) -> None:
        workspace = self.mod.ensure_workspace("draft-1")
        self.assertTrue(workspace.is_dir())
        self.assertTrue((workspace / ".git").exists())
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "true")

    def test_workspace_is_outside_the_target_repository(self) -> None:
        workspace = self.mod.ensure_workspace("draft-1")
        self.assertNotIn(str(self.target_repo.resolve()), str(workspace.resolve()))
        self.assertFalse(str(workspace.resolve()).startswith(str(self.target_repo.resolve())))

    def test_saving_a_draft_never_dirties_the_target_repo(self) -> None:
        self.mod.save_draft(
            "draft-1",
            "# Title\n\nBody.\n",
            {"repo": "octo/widgets"},
        )
        self.assertTrue(self._target_repo_is_clean())

    def test_save_and_load_round_trip_markdown_and_metadata(self) -> None:
        self.mod.save_draft(
            "draft-1",
            "# Title\n\nBody.\n",
            {"repo": "octo/widgets", "labels": ["bug"]},
        )
        self.assertEqual(self.mod.load_draft("draft-1"), "# Title\n\nBody.\n")
        self.assertEqual(
            self.mod.load_metadata("draft-1"),
            {"repo": "octo/widgets", "labels": ["bug"]},
        )

    def test_same_draft_id_reuses_the_same_workspace(self) -> None:
        first = self.mod.ensure_workspace("draft-1")
        self.mod.save_draft("draft-1", "# Title\n\nBody.\n", {"repo": "octo/widgets"})
        second = self.mod.ensure_workspace("draft-1")
        self.assertEqual(first.resolve(), second.resolve())
        # Reusing the workspace must not wipe an already-saved draft.
        self.assertEqual(self.mod.load_draft("draft-1"), "# Title\n\nBody.\n")

    def test_same_draft_id_reuses_the_same_git_history(self) -> None:
        self.mod.ensure_workspace("draft-1")
        first_head = subprocess.run(
            ["git", "-C", str(self.mod.draft_dir("draft-1")), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        # A second call must not re-``git init`` (which would create a fresh
        # object database and a new, unrelated baseline commit) -- it must be
        # the exact same repository at the exact same commit.
        second_head = subprocess.run(
            ["git", "-C", str(self.mod.ensure_workspace("draft-1")), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(first_head.returncode, 0, "workspace must already have a baseline commit")
        self.assertEqual(second_head.returncode, 0)
        self.assertEqual(first_head.stdout.strip(), second_head.stdout.strip())

    def test_workspace_has_a_valid_baseline_head_before_any_draft_is_saved(self) -> None:
        """tuicr needs something to diff the draft against; a workspace with
        no commits at all has no baseline for that diff.
        """
        workspace = self.mod.ensure_workspace("draft-1")
        result = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"expected a resolvable baseline HEAD, got: {result.stderr.strip()!r}",
        )

    def test_save_draft_leaves_draft_and_metadata_as_working_tree_changes(self) -> None:
        """The human review happens on the *unstaged* diff in tuicr. If
        ``save_draft`` auto-commits, there is nothing left to review and no
        working-tree change for a close report to cover.
        """
        self.mod.save_draft(
            "draft-1", "# Title\n\nBody.\n", {"repo": "octo/widgets"}
        )
        workspace = self.mod.draft_dir("draft-1")
        status = subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        changed_paths = {line[3:] for line in status.splitlines() if line.strip()}
        self.assertIn("draft.md", changed_paths, "draft.md must be a working-tree change")
        self.assertIn("metadata.json", changed_paths, "metadata.json must be a working-tree change")
        # Nothing should already be staged/committed on top of the baseline --
        # the diff against HEAD must show the new content as pending.
        diff = subprocess.run(
            [
                "git", "-C", str(workspace), "diff", "--quiet", "HEAD",
                "--", "draft.md", "metadata.json",
            ]
        )
        self.assertNotEqual(
            diff.returncode, 0,
            "draft.md/metadata.json must differ from HEAD (uncommitted) for tuicr to review",
        )

    def test_updating_a_saved_draft_remains_uncommitted(self) -> None:
        self.mod.save_draft("draft-1", "# Title\n\nBody.\n", {"repo": "octo/widgets"})
        self.mod.save_draft("draft-1", "# Title\n\nUpdated body.\n", {"repo": "octo/widgets"})
        workspace = self.mod.draft_dir("draft-1")
        status = subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertTrue(
            status.strip(),
            "updating a draft must remain a working-tree change, not be auto-committed",
        )

    def test_workspace_is_reused_across_save_and_review_without_extra_commits(self) -> None:
        """The same repo/session is reused across draft revisions -- there
        must be exactly one baseline commit (from workspace creation), even
        after multiple saves, because saves must never themselves commit.
        """
        self.mod.save_draft("draft-1", "# Title\n\nBody.\n", {"repo": "octo/widgets"})
        self.mod.save_draft("draft-1", "# Title\n\nUpdated body.\n", {"repo": "octo/widgets"})
        workspace = self.mod.draft_dir("draft-1")
        log = subprocess.run(
            ["git", "-C", str(workspace), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        commit_count = len([line for line in log.splitlines() if line.strip()])
        self.assertEqual(
            commit_count,
            1,
            "only the baseline commit should exist; saves must not add commits",
        )

    def test_different_draft_ids_get_different_workspaces(self) -> None:
        first = self.mod.ensure_workspace("draft-1")
        second = self.mod.ensure_workspace("draft-2")
        self.assertNotEqual(first.resolve(), second.resolve())

    def test_load_draft_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.mod.load_draft("never-created")


if __name__ == "__main__":
    unittest.main(verbosity=2)
