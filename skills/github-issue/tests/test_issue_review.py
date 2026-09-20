#!/usr/bin/env python3
"""Executable contract for the publication authorization gate: ``lib/issue_review.py``.

This is the single choke point between a drafted issue and the MCP
``issue_write`` call. Pins:

- Only an ``approved`` tuicr close report for the *exact* current draft
  workspace/target and an *unchanged* HEAD, with every reported file reviewed
  and no unanswered comments, authorizes publication.
- Incomplete/aborted verdicts, stale or absent reports, unreviewed files,
  target/draft mismatches, HEAD drift, and unanswered comments are all
  refused with a legible reason.
- A fresh duplicate search performed immediately before ``issue_write`` is
  mandatory; an open duplicate refuses publication outright.
- The published body retains the reviewed draft's final provenance table --
  the human explicitly wants a small provenance record at the bottom of the
  filed issue. ``strip_provenance`` remains a separate title+body-only
  utility; it is not applied on the publication path.
- An authorization is bound to the *exact* draft content and target repo it
  was granted for: neither may be swapped for something else at payload-
  assembly time, even though the low-level `authorize_publication` call
  already returned ``authorized=True`` for the original pair.
- That binding must come only from data the caller explicitly and verifiably
  supplies. Recovering "reviewed" content by guessing at same-named local
  variables in the calling stack is not a security boundary: any caller,
  malicious or merely coincidental, can have a local named ``draft_markdown``
  holding arbitrary content, so such a binding authorizes whatever the caller
  wants rather than what a human actually reviewed.
"""

from __future__ import annotations

import importlib.util
import sys
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


DRAFT_PATH = "/state/agents/github-issue/draft-1/draft.md"
TARGET_REPO = "octo/widgets"
HEAD = "abc123"


def base_report(**overrides):
    report = {
        "verdict": "approved",
        "approved": True,
        "draft_path": DRAFT_PATH,
        "target_repo": TARGET_REPO,
        "head_after": HEAD,
        "marks": {
            "files": [{"path": DRAFT_PATH, "state": "reviewed"}],
        },
        "unanswered": [],
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
        result = self.authorize(base_report(head_after="def456"))
        self.assertFalse(result.authorized)
        self.assertTrue(any("head" in r.lower() for r in result.reasons))

    def test_wrong_target_repo_is_refused(self) -> None:
        result = self.authorize(base_report(target_repo="someone/else"))
        self.assertFalse(result.authorized)

    def test_wrong_draft_path_is_refused(self) -> None:
        result = self.authorize(base_report(draft_path="/state/agents/github-issue/draft-2/draft.md"))
        self.assertFalse(result.authorized)

    def test_unreviewed_file_is_refused(self) -> None:
        result = self.authorize(
            base_report(marks={"files": [{"path": DRAFT_PATH, "state": "unreviewed"}]})
        )
        self.assertFalse(result.authorized)

    def test_partially_reviewed_files_are_refused(self) -> None:
        result = self.authorize(
            base_report(
                marks={
                    "files": [
                        {"path": DRAFT_PATH, "state": "reviewed"},
                        {"path": "other.md", "state": "unreviewed"},
                    ]
                }
            )
        )
        self.assertFalse(result.authorized)

    def test_no_reviewed_files_at_all_is_refused(self) -> None:
        result = self.authorize(base_report(marks={"files": []}))
        self.assertFalse(result.authorized)

    def test_unanswered_comments_are_refused(self) -> None:
        result = self.authorize(base_report(unanswered=[{"id": "c1"}]))
        self.assertFalse(result.authorized)
        self.assertTrue(any("unanswered" in r.lower() for r in result.reasons))

    def test_refusal_reasons_are_all_reported_together(self) -> None:
        result = self.authorize(
            base_report(head_after="def456", unanswered=[{"id": "c1"}])
        )
        self.assertFalse(result.authorized)
        self.assertGreaterEqual(len(result.reasons), 2)


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
