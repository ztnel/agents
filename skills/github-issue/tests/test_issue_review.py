#!/usr/bin/env python3
"""Publication requires a generic approval receipt covering unchanged inputs.

Pins draft and metadata binding, duplicate refusal, retained provenance, and
integration with the current review adapter's real approval producer.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import subprocess
import json
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"


def _load(name: str):
    path = LIB / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


WORKSPACE = tempfile.TemporaryDirectory()
DRAFT_PATH = str(Path(WORKSPACE.name) / "draft.md")
TARGET_REPO = "octo/widgets"
subprocess.run(["git", "init", "-q", WORKSPACE.name], check=True)
subprocess.run(
    ["git", "-C", WORKSPACE.name, "-c", "user.name=Test", "-c", "user.email=test@example.com",
     "commit", "--allow-empty", "-qm", "Baseline"], check=True,
)
HEAD = subprocess.check_output(["git", "-C", WORKSPACE.name, "rev-parse", "HEAD"], text=True).strip()


def base_report(**overrides):
    draft = Path(DRAFT_PATH)
    if not draft.exists():
        draft.write_text("test draft", encoding="utf-8")
    metadata = draft.with_name("metadata.json")
    metadata.write_text(json.dumps({"repo": TARGET_REPO}), encoding="utf-8")
    report = {
        "schema": "agents.approval/v1",
        "approved": True,
        "workspace": WORKSPACE.name,
        "head": HEAD,
        "files": [
            {"path": path.name,
             "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in (draft, metadata)
        ],
    }
    report.update(overrides)
    return report


class AuthorizePublicationTest(unittest.TestCase):
    """The close-report gate: ``authorize_publication``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load("issue_review")

    def authorize(self, report):
        return self.mod.authorize_publication(
            report,
            draft_path=DRAFT_PATH,
            target_repo=TARGET_REPO,
            current_head=HEAD,
        )

    def test_clean_approved_report_authorizes(self) -> None:
        result = self.authorize(base_report())
        self.assertTrue(result.authorized)
        self.assertEqual(result.reasons, [])

    def test_incomplete_verdict_is_refused(self) -> None:
        result = self.authorize(base_report(verdict="incomplete", approved=False))
        self.assertFalse(result.authorized)
        self.assertTrue(result.reasons)

    def test_aborted_verdict_is_refused(self) -> None:
        result = self.authorize(base_report(verdict="aborted", approved=False))
        self.assertFalse(result.authorized)

    def test_absent_report_is_refused(self) -> None:
        result = self.mod.authorize_publication(
            None, draft_path=DRAFT_PATH, target_repo=TARGET_REPO, current_head=HEAD
        )
        self.assertFalse(result.authorized)
        self.assertTrue(result.reasons)

    def test_stale_head_is_refused(self) -> None:
        result = self.authorize(base_report(head="def456"))
        self.assertFalse(result.authorized)
        self.assertTrue(any("head" in r.lower() for r in result.reasons))

    def test_wrong_target_repo_is_refused(self) -> None:
        report = base_report()
        Path(DRAFT_PATH).with_name("metadata.json").write_text('{"repo":"someone/else"}')
        result = self.authorize(report)
        self.assertFalse(result.authorized)

    def test_wrong_draft_path_is_refused(self) -> None:
        result = self.authorize(base_report(workspace="/state/agents/github-issue/draft-2"))
        self.assertFalse(result.authorized)

    def test_unreviewed_file_is_refused(self) -> None:
        result = self.authorize(
            base_report(files=[])
        )
        self.assertFalse(result.authorized)

    def test_partially_reviewed_files_are_refused(self) -> None:
        result = self.authorize(
            base_report(
                files=[{"path": "draft.md", "content_sha256": "0" * 64}]
            )
        )
        self.assertFalse(result.authorized)

    def test_no_reviewed_files_at_all_is_refused(self) -> None:
        result = self.authorize(base_report(files=[]))
        self.assertFalse(result.authorized)

    def test_orientation_comments_do_not_override_approved_verdict(self) -> None:
        result = self.authorize(base_report(unanswered=[{"id": "c1"}]))
        self.assertTrue(result.authorized)

    def test_mismatched_workspace_is_rejected_before_reading_inputs(self) -> None:
        result = self.authorize(
            base_report(head="def456", workspace="/wrong/workspace")
        )
        self.assertFalse(result.authorized)
        self.assertIn("workspace", result.reasons[0])

    def test_real_tuicr_close_report_authorizes(self) -> None:
        tuicr_lib = LIB.parents[1] / "tuicr" / "lib"
        sys.path.insert(0, str(tuicr_lib))
        import tuicr_up
        draft_module = _load("issue_draft")
        markdown = draft_module.render_draft(
            title="Generic approval integration", body="Approved issue content.",
            identified_by="gpt-6.1-sol", authored_by=["gpt-6.1-sol"],
            approved_by="reviewer@example.com",
        )
        Path(DRAFT_PATH).write_text(markdown, encoding="utf-8")
        initial = base_report()
        marks = {"ok": True, "files": [
            {**file, "state": "reviewed"} for file in initial["files"]
        ]}
        response = SimpleNamespace(ok=True, stdout=json.dumps(marks), stderr="")
        with patch.object(tuicr_up, "run", return_value=response):
            with patch.object(tuicr_up, "git", return_value=SimpleNamespace(ok=True, stdout=HEAD)):
                with patch.object(tuicr_up.questions, "unanswered", return_value=[]):
                    report = tuicr_up.review_verdict(WORKSPACE.name, "review-1", HEAD, 0)
        self.assertNotIn("draft_path", report)
        self.assertNotIn("target_repo", report)
        authorization = self.mod.authorize_publication(
            report["approval"], draft_path=DRAFT_PATH, target_repo=TARGET_REPO,
            current_head=HEAD, draft_markdown=markdown,
        )
        payload = self.mod.prepare_issue_payload(
            authorization=authorization, draft_markdown=markdown,
            metadata={"repo": TARGET_REPO}, duplicate_matches=[],
        )
        self.assertEqual(payload["title"], "Generic approval integration")
        self.assertEqual(payload["repo"], TARGET_REPO)

    def test_metadata_and_draft_edits_after_approval_are_refused(self) -> None:
        for name in ("draft.md", "metadata.json"):
            report = base_report()
            path = Path(WORKSPACE.name) / name
            original = path.read_text()
            path.write_text(original + " ")
            self.assertFalse(self.authorize(report).authorized)
            path.write_text(original)

    def test_metadata_must_be_reviewed(self) -> None:
        report = base_report()
        report["files"] = report["files"][:1]
        self.assertFalse(self.authorize(report).authorized)

    def test_missing_content_fingerprint_is_refused(self) -> None:
        report = base_report()
        del report["files"][0]["content_sha256"]
        self.assertFalse(self.authorize(report).authorized)


class DuplicateGuardTest(unittest.TestCase):
    """The mandatory fresh duplicate search performed right before publish."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load("issue_review")

    def test_no_matches_passes(self) -> None:
        self.mod.guard_duplicate_search([])

    def test_only_closed_matches_pass(self) -> None:
        self.mod.guard_duplicate_search([{"number": 42, "state": "closed"}])

    def test_open_match_raises_duplicate_error(self) -> None:
        with self.assertRaises(self.mod.DuplicateIssueError):
            self.mod.guard_duplicate_search([{"number": 7, "state": "open"}])

    def test_duplicate_error_names_the_issue(self) -> None:
        with self.assertRaises(self.mod.DuplicateIssueError) as ctx:
            self.mod.guard_duplicate_search([{"number": 7, "state": "open"}])
        self.assertIn("7", str(ctx.exception))


class PrepareIssuePayloadTest(unittest.TestCase):
    """Only reviewed body and validated metadata reach ``issue_write``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.issue_review = _load("issue_review")
        cls.issue_draft = _load("issue_draft")

    def setUp(self) -> None:
        self.draft_markdown = self.issue_draft.render_draft(
            title="Flaky upload retries under load",
            body="## Summary\n\nUploads intermittently 500 under concurrent load.\n",
            identified_by="reporter@example.com",
            authored_by=["gpt-5.1", "claude-sonnet-5"],
            approved_by="approver@example.com",
        )
        Path(DRAFT_PATH).write_text(self.draft_markdown, encoding="utf-8")
        self.authorized = self.issue_review.authorize_publication(
            base_report(),
            draft_path=DRAFT_PATH,
            target_repo=TARGET_REPO,
            current_head=HEAD,
            draft_markdown=self.draft_markdown,
        )
        self.assertTrue(self.authorized.authorized)

    def test_denied_authorization_raises_publication_denied(self) -> None:
        denied = self.issue_review.authorize_publication(
            base_report(verdict="incomplete", approved=False),
            draft_path=DRAFT_PATH,
            target_repo=TARGET_REPO,
            current_head=HEAD,
        )
        with self.assertRaises(self.issue_review.PublicationDenied):
            self.issue_review.prepare_issue_payload(
                authorization=denied,
                draft_markdown=self.draft_markdown,
                metadata={"repo": TARGET_REPO},
                duplicate_matches=[],
            )

    def test_open_duplicate_blocks_publication(self) -> None:
        with self.assertRaises(self.issue_review.DuplicateIssueError):
            self.issue_review.prepare_issue_payload(
                authorization=self.authorized,
                draft_markdown=self.draft_markdown,
                metadata={"repo": TARGET_REPO},
                duplicate_matches=[{"number": 9, "state": "open"}],
            )

    def test_authorized_clean_draft_yields_title_and_body_with_provenance(self) -> None:
        """The human explicitly requires a small provenance table at the
        bottom of the published issue -- publication must retain it, not
        strip it. ``strip_provenance`` remains a separate utility for callers
        that want title+body only; it is not what ``issue_write`` receives.
        """
        payload = self.issue_review.prepare_issue_payload(
            authorization=self.authorized,
            draft_markdown=self.draft_markdown,
            metadata={"repo": TARGET_REPO},
            duplicate_matches=[],
        )
        self.assertEqual(payload["title"], "Flaky upload retries under load")
        self.assertIn("Uploads intermittently", payload["body"])
        self.assertIn("## Provenance", payload["body"])
        for label in ("Identified by", "Authored by", "Approved by"):
            self.assertIn(label, payload["body"])
        self.assertIn("reporter@example.com", payload["body"])
        self.assertEqual(payload["repo"], TARGET_REPO)

    def test_missing_required_metadata_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.issue_review.prepare_issue_payload(
                authorization=self.authorized,
                draft_markdown=self.draft_markdown,
                metadata={},
                duplicate_matches=[],
            )

    def test_placeholder_model_id_in_draft_is_rejected_even_if_authorized(self) -> None:
        bad_draft = self.draft_markdown.replace("gpt-5.1", "copilot-auto")
        with self.assertRaises(self.issue_draft.ProvenanceError):
            self.issue_review.prepare_issue_payload(
                authorization=self.authorized,
                draft_markdown=bad_draft,
                metadata={"repo": TARGET_REPO},
                duplicate_matches=[],
            )

    def test_authorized_draft_content_cannot_be_swapped_at_prepare_time(self) -> None:
        """Authorization is granted for one exact draft; publishing a different,
        never-reviewed draft under that authorization is exactly the "changed
        content" case the close-report gate exists to reject (requirement 5).
        An ``AuthorizationResult`` that carries no binding to what was actually
        reviewed lets any well-formed markdown ride through on someone else's
        approval.
        """
        unrelated_draft = self.issue_draft.render_draft(
            title="Completely unrelated, never-reviewed report",
            body="## Summary\n\nThis draft was never shown to a human reviewer.\n",
            identified_by="reporter@example.com",
            authored_by=["gpt-5.1"],
            approved_by="approver@example.com",
        )
        with self.assertRaises((self.issue_review.PublicationDenied, ValueError)):
            self.issue_review.prepare_issue_payload(
                authorization=self.authorized,
                draft_markdown=unrelated_draft,
                metadata={"repo": TARGET_REPO},
                duplicate_matches=[],
            )

    def test_authorized_target_repo_binds_the_published_metadata_repo(self) -> None:
        """Authorization for ``octo/widgets`` must not let the same authorization
        publish to a different ``metadata['repo']`` -- that is the "target
        mismatch" case requirement 5 requires the gate to reject, not just at
        the close-report level but at the point the payload is actually
        assembled for ``issue_write``.
        """
        with self.assertRaises((self.issue_review.PublicationDenied, ValueError)):
            self.issue_review.prepare_issue_payload(
                authorization=self.authorized,
                draft_markdown=self.draft_markdown,
                metadata={"repo": "someone-else/hostile"},
                duplicate_matches=[],
            )

    def test_metadata_labels_cannot_be_swapped_after_authorization(self) -> None:
        with self.assertRaises(self.issue_review.PublicationDenied):
            self.issue_review.prepare_issue_payload(
                authorization=self.authorized, draft_markdown=self.draft_markdown,
                metadata={"repo": TARGET_REPO, "labels": ["unreviewed"]}, duplicate_matches=[],
            )


class AuthorizationBindingIntegrityTest(unittest.TestCase):
    """The reviewed-content binding must come only from data the caller
    explicitly and verifiably supplies to ``authorize_publication`` -- never
    from guessing at same-named local variables in the calling stack. Stack
    introspection cannot tell a genuinely reviewed draft from any other string
    a caller happens to name ``draft_markdown``, so it authorizes whatever the
    caller wants rather than what a human actually reviewed.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load("issue_review")
        cls.issue_draft = _load("issue_draft")

    def test_binding_ignores_unrelated_same_named_caller_locals(self) -> None:
        # A caller frame may hold an unrelated string in a variable literally
        # named `draft_markdown` for a reason that has nothing to do with
        # review -- any real codebase can hit this by coincidence. Calling
        # authorize_publication without an explicit draft_markdown keyword
        # must never silently bind to it.
        draft_markdown = "not a reviewed draft; an unrelated local variable"  # noqa: F841
        auth = self.mod.authorize_publication(
            base_report(), draft_path=DRAFT_PATH, target_repo=TARGET_REPO, current_head=HEAD
        )
        self.assertIsNone(
            auth.reviewed_draft_sha256,
            "authorization must not recover reviewed content via stack introspection "
            "of same-named caller locals",
        )

    def test_stack_introspection_cannot_forge_a_reviewed_binding(self) -> None:
        """End-to-end proof: content nobody ever reviewed, self-approved by its
        own author, must not reach a preparable ``issue_write`` payload merely
        because some caller frame happens to hold it in a variable named
        ``draft_markdown``.
        """
        forged_draft = self.issue_draft.render_draft(
            title="Forged issue nobody reviewed",
            body="## Summary\n\nThis was never shown to a human reviewer.\n",
            identified_by="attacker@example.com",
            authored_by=["gpt-5.1"],
            approved_by="attacker@example.com",
        )

        def attacker_controlled_caller():
            draft_markdown = forged_draft  # matches the recovery heuristic's target name
            return self.mod.authorize_publication(
                base_report(), draft_path=DRAFT_PATH, target_repo=TARGET_REPO, current_head=HEAD
            )

        auth = attacker_controlled_caller()
        with self.assertRaises((self.mod.PublicationDenied, ValueError)):
            self.mod.prepare_issue_payload(
                authorization=auth,
                draft_markdown=forged_draft,
                metadata={"repo": TARGET_REPO},
                duplicate_matches=[],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
