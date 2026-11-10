#!/usr/bin/env python3
"""Durable feedback delivery across edits, work, closure, failure and restart."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillkit import workqueue  # noqa: E402
from skillkit.feedback import Inbox, Message, Review, Reviews, version  # noqa: E402


def review(text="first", closed=False):
    return Review(
        frontend="fake", closed=closed, context={},
        pending=[Message(
            id="human", thread="thread", anchor={"start": 1},
            content=text, version=version(text),
        )] if text else [],
    )


class Fake:
    frontend = "fake"
    semantics = "test"
    closure_detection = True
    identity = "fake-review"

    def __init__(self, current):
        self.current = current
        self.reads = 0

    async def read(self):
        self.reads += 1
        if isinstance(self.current, Exception):
            raise self.current
        return self.current

    async def reply(self, target, content, author):
        self.replied = (target, content, author)
        return {"ok": True}


class Batching(Fake):
    queue_scope = workqueue.scope("repo", "slug")
    policy = "Work waits for closure."

    def __init__(self, current):
        super().__init__(current)
        self.tree = "clean"

    async def fingerprint(self):
        return self.tree


class InboxTest(unittest.TestCase):
    def test_restart_replays_batch_and_acknowledges_only_delivered_events(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "inbox.db"
            inbox = Inbox(path)
            inbox.observe("review", review())
            batch = inbox.receive("review").batch
            inbox.observe("review", review("edited"))
            inbox.close()
            inbox = Inbox(path)
            self.addCleanup(inbox.close)
            replay = inbox.receive("review").batch
            self.assertEqual(replay.id, batch.id)
            self.assertTrue(replay.events[0].superseded)
            with self.assertRaisesRegex(ValueError, "belong"):
                inbox.acknowledge("other-review", batch.id)
            inbox.acknowledge("review", batch.id)
            inbox.acknowledge("review", batch.id)
            next_batch = inbox.receive("review").batch
            self.assertNotEqual(next_batch.id, batch.id)
            self.assertEqual(next_batch.events[0].message.content, "edited")
            inbox.acknowledge("review", next_batch.id)
            inbox.observe("review", review("edited"))
            self.assertEqual(inbox.receive("review").status, "timeout")

    def test_unacknowledged_closure_is_superseded_when_the_review_reopens(self):
        inbox = Inbox()
        self.addCleanup(inbox.close)
        inbox.observe("review", review(None))
        inbox.observe("review", review(None, True))
        self.assertFalse(inbox.receive("review").batch.events[0].superseded)
        inbox.observe("review", review("next round"))
        replay = inbox.receive("review").batch
        self.assertEqual([e.kind for e in replay.events], ["closure"])
        self.assertTrue(replay.events[0].superseded)

    def test_withdrawal_and_closure_preserve_outstanding_feedback(self):
        inbox = Inbox()
        self.addCleanup(inbox.close)
        inbox.observe("review", review())
        first = inbox.receive("review").batch
        inbox.observe("review", review(None))
        closed = review(None, True)
        closed.closure = {"verdict": "incomplete"}
        inbox.observe("review", closed)
        self.assertTrue(inbox.receive("review").batch.events[0].superseded)
        inbox.acknowledge("review", first.id)
        final = inbox.receive("review")
        self.assertTrue(final.closed)
        self.assertEqual([event.kind for event in final.batch.events], ["withdrawn", "closure"])
        self.assertEqual(final.batch.events[1].closure, {"verdict": "incomplete"})
        inbox.acknowledge("review", final.batch.id)
        self.assertEqual(inbox.receive("review").status, "closed")

    def test_reopening_a_closed_review_is_not_a_closure_event(self):
        inbox = Inbox()
        self.addCleanup(inbox.close)
        inbox.observe("review", review(None, True))
        inbox.acknowledge("review", inbox.receive("review").batch.id)
        inbox.observe("review", review(None, None))
        self.assertEqual(inbox.receive("review").status, "timeout")
        inbox.observe("review", review("rework"))
        self.assertEqual([e.kind for e in inbox.receive("review").batch.events], ["message"])

    def test_observed_revisions_are_ordered_and_bounded(self):
        inbox = Inbox()
        self.addCleanup(inbox.close)
        for index in range(60):
            inbox.observe("review", review(str(index), None))
        batch = inbox.receive("review").batch
        self.assertEqual(len(batch.events), 50)
        self.assertTrue(all(event.superseded for event in batch.events))
        inbox.acknowledge("review", batch.id)
        final = inbox.receive("review").batch
        self.assertEqual(len(final.events), 10)
        self.assertFalse(final.events[-1].superseded)

    def test_reverting_an_edit_does_not_revive_an_old_queued_request(self):
        inbox = Inbox()
        self.addCleanup(inbox.close)
        inbox.observe("review", review("first"))
        inbox.observe("review", review("edited"))
        inbox.observe("review", review("first"))
        events = inbox.receive("review").batch.events
        self.assertEqual([event.superseded for event in events], [True, True, False])


class ReviewsTest(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_failure_is_not_reported_as_empty_feedback(self):
        connector = Fake(review(None))
        reviews = Reviews(Inbox())
        review_id = (await reviews.attach(connector))["review_id"]
        connector.current = RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await asyncio.wait_for(reviews.monitors[review_id], 3)
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await reviews.receive(review_id)
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await reviews.close()

    async def test_monitor_collects_while_agent_works_and_cancel_replays(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "inbox.db"
            connector = Fake(review(None))
            reviews = Reviews(Inbox(path))
            try:
                review_id = (await reviews.attach(connector))["review_id"]
                waiting = asyncio.create_task(reviews.wait(review_id, 10, "agent"))
                await asyncio.sleep(.02)
                waiting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiting
                connector.current = review()
                await asyncio.sleep(1.1)
                first = reviews.inbox.receive(review_id).batch
                self.assertIsNotNone(first)
                self.assertEqual((await reviews.receive(review_id)).batch.id, first.id)
            finally:
                await reviews.close()
            fresh = Reviews(Inbox(path))
            try:
                self.assertEqual((await fresh.attach(connector))["review_id"], review_id)
                self.assertEqual((await fresh.receive(review_id)).batch.id, first.id)
            finally:
                await fresh.close()

    async def test_closure_stops_the_monitor_and_later_calls_use_the_inbox(self):
        connector = Fake(review())
        reviews = Reviews(Inbox())
        self.addAsyncCleanup(reviews.close)
        review_id = (await reviews.attach(connector))["review_id"]
        first = (await reviews.receive(review_id)).batch
        reviews.inbox.acknowledge(review_id, first.id)
        connector.current = review(None, True)
        await asyncio.wait_for(reviews.monitors[review_id], 3)
        reads = connector.reads
        connector.current = RuntimeError("surface is gone")
        final = await reviews.wait(review_id, 30, "agent")
        self.assertTrue(final.closed)
        self.assertTrue((await reviews.read(review_id)).closed)
        with self.assertRaisesRegex(ValueError, "closed"):
            await reviews.reply(review_id, "human", "v", "answered", "Answer", "agent")
        self.assertEqual(connector.reads, reads)
        connector.current = review("reopened")
        self.assertFalse((await reviews.attach(connector))["closed"])
        self.assertFalse(reviews.monitors[review_id].done())

    async def test_connect_reports_pending_messages_and_next_step(self):
        reviews = Reviews(Inbox())
        self.addAsyncCleanup(reviews.close)
        opened = await reviews.attach(Fake(review()))
        self.assertEqual((opened["pending"], opened["closed"]), (1, False))
        self.assertIn("wait_for_feedback", opened["next"])

    async def test_reply_rejects_stale_unknown_and_closed_targets(self):
        connector = Fake(review())
        reviews = Reviews(Inbox())
        self.addAsyncCleanup(reviews.close)
        review_id = (await reviews.attach(connector))["review_id"]
        target = connector.current.pending[0]
        await reviews.reply(review_id, "human", target.version, "answered", "Answer", "agent")
        self.assertEqual(connector.replied[0].anchor, {"start": 1})
        with self.assertRaisesRegex(ValueError, "changed"):
            await reviews.reply(review_id, "human", "old", "answered", "Answer", "agent")
        with self.assertRaisesRegex(ValueError, "not pending"):
            await reviews.reply(review_id, "other", target.version, "answered", "Answer", "agent")
        connector.current = review(closed=True)
        with self.assertRaisesRegex(ValueError, "closed"):
            await reviews.reply(review_id, "human", target.version, "answered", "Answer", "agent")

    async def test_verdicts_queue_accepted_work_and_edits_wait_for_closure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "inbox.db"
            connector = Batching(review())
            reviews = Reviews(Inbox(path))
            self.addAsyncCleanup(reviews.close)
            opened = await reviews.attach(connector)
            review_id = opened["review_id"]
            self.assertIn("Work waits for closure.", opened["next"])
            target = connector.current.pending[0]
            for verdict, task in (("accepted", None), ("answered", "work"), ("maybe", None)):
                with self.assertRaisesRegex(ValueError, "verdict|task"):
                    await reviews.reply(
                        review_id, "human", target.version, verdict, "Why", "agent", task)
            connector.tree = "edited"
            with self.assertRaisesRegex(ValueError, "changed while the review is open"):
                await reviews.reply(
                    review_id, "human", target.version, "accepted", "Why", "agent", "Fix it")
            with self.assertRaisesRegex(ValueError, "changed while the review is open"):
                await reviews.wait(review_id, 1, "agent")
            await reviews.attach(connector)
            await reviews.reply(
                review_id, "human", target.version, "accepted", "Why", "agent", "Fix it")
            self.assertTrue(connector.replied[1].startswith("**Queued** — Fix it\n\n"))
            self.assertEqual(
                workqueue.pending(path, connector.queue_scope),
                [{"message_id": "human", "task": "Fix it"}])
            connector.current = review(None, True)
            await asyncio.wait_for(reviews.monitors[review_id], 3)
            connector.tree = "applying the queue"
            self.assertTrue((await reviews.wait(review_id, 1, "agent")).closed)
            workqueue.archive(path, connector.queue_scope)
            self.assertEqual(workqueue.pending(path, connector.queue_scope), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
