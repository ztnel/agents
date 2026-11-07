#!/usr/bin/env python3
"""tuicr feedback MCP server: native conformance plus connector edge cases."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path[:0] = [str(LIB), str(Path(__file__).resolve().parents[2] / "_lib")]
import feedback_server  # noqa: E402
import tuicr_up  # noqa: E402
from skillkit import paths  # noqa: E402
from skillkit.feedback_conformance import Conformance  # noqa: E402


class Isolated:
    def isolate(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, {
            "HOME": str(self.root), "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_DATA_HOME": str(self.root / "data"),
        })
        env.start()
        self.addCleanup(env.stop)


@unittest.skipUnless(shutil.which("tuicr"), "native tuicr executable not installed")
class TuicrConformanceTest(Isolated, Conformance, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.isolate()
        fixture = self.root / "fixture.json"
        fixture.write_text(json.dumps({
            "version": "1.3", "id": "feedback-test-review", "files": {},
            "repo_path": str(self.root), "base_commit": "abc", "head_commit": "abc",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        }))
        self.tuicr("add", "--session", fixture, "--username", "Tester", "--", "Setup")
        (listed,) = json.loads(self.tuicr("list", "--repo", self.root))
        self.slug, self.session = listed["slug"], listed["path"]
        self.connect_arguments = {"repo": str(self.root), "session": self.slug}

    def tuicr(self, *argv):
        return subprocess.run(
            ["tuicr", "review", *map(str, argv)], capture_output=True, text=True,
            timeout=20, check=True,
        ).stdout

    def build(self, reviews):
        return feedback_server.build(reviews)

    async def human_comment(self, content):
        self.tuicr("add", "--session", self.session, "--username", "Tester", "--", content)

    async def close_natively(self):
        report = tuicr_up.report_path(str(self.root), self.slug)
        verdict = tuicr_up.review_verdict(str(self.root), self.slug, "", 0)
        paths.write_json_atomic(report, verdict, indent=2)

    async def drain(self, client, review_id):
        # The setup comment is pending until answered; answer it first.
        current = await self.call(client, "read_review", review_id=review_id)
        for item in current["pending"]:
            await self.call(
                client, "reply", review_id=review_id, message_id=item["id"],
                message_version=item["version"], content="Ack", author="test-agent",
            )
        await super().drain(client, review_id)


class TuicrConnectorTest(Isolated, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.isolate()
        self.connector = feedback_server.Tuicr(str(self.root), "slug", self.root / "s.json")
        self.comment = {
            "id": "human", "content": "Question", "comment_type": "note",
            "created_at": "2026-01-01T00:00:00Z", "path": "a.py",
            "start_line": 2, "end_line": 5, "side": "old",
        }

    async def test_context_and_registered_questions_are_never_pending(self):
        context = {**self.comment, "id": "context", "comment_type": "review-note"}
        asked = {**self.comment, "id": "asked"}
        with patch.object(feedback_server, "command", AsyncMock(return_value=[self.comment, context, asked])), \
                patch.object(feedback_server.questions, "registered_ids", return_value={"asked"}):
            review = await self.connector.read()
        self.assertEqual([(m.id, m.comment_type) for m in review.pending], [("human", "note")])
        self.assertIsNone(review.closed)

    async def test_reply_preserves_range_side_and_verifies_delivery(self):
        with patch.object(feedback_server, "command", AsyncMock(return_value=[self.comment])):
            target = (await self.connector.read()).pending[0]
        delivered = {**self.comment, "id": "agent", "comment_type": "reply", "created_at": "2026-01-02T00:00:00Z"}
        with patch.object(feedback_server, "command", AsyncMock(side_effect=[delivered, [self.comment, delivered]])) as run:
            await self.connector.reply(target, "-literal reply", "test-agent")
        argv = run.await_args_list[0].args[0]
        self.assertEqual(argv[argv.index("--end-line") + 1], "5")
        self.assertEqual(argv[argv.index("--side") + 1], "old")
        self.assertEqual(argv[-2:], ["--", "-literal reply"])
        with patch.object(feedback_server, "command", AsyncMock(side_effect=[delivered, [self.comment]])):
            with self.assertRaisesRegex(RuntimeError, "absent"):
                await self.connector.reply(target, "Answer", "test-agent")

    async def test_close_report_is_closure_detail_not_approval(self):
        report = tuicr_up.report_path(str(self.root), "slug")
        paths.write_json_atomic(report, {"verdict": "incomplete", "approved": False})
        with patch.object(feedback_server, "command", AsyncMock(return_value=[])):
            review = await self.connector.read()
        self.assertTrue(review.closed)
        self.assertEqual(review.closure, {"verdict": "incomplete", "report": str(report)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
