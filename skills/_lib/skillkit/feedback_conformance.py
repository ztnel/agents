"""Conformance suite every review-feedback connector must pass natively.

Subclass :class:`Conformance` together with ``unittest.IsolatedAsyncioTestCase``
and implement the three native hooks against a real frontend. The suite checks
the shared contract end to end through MCP: a human comment arrives once as a
durable batch, replays until acknowledged (also after a server restart), a reply
at the original anchor withdraws it, and closure is delivered without approval.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from .feedback import Inbox, Reviews

TOOLS = {
    "connect_review", "read_review", "receive_feedback", "acknowledge_feedback",
    "reply", "wait_for_feedback",
}


class Conformance:
    #: Arguments for this frontend's ``connect_review`` tool.
    connect_arguments: dict
    #: Directory for the inbox database; set in ``asyncSetUp``.
    root: Path

    def build(self, reviews: Reviews) -> FastMCP:
        raise NotImplementedError

    async def human_comment(self, content: str) -> None:
        raise NotImplementedError

    async def close_natively(self) -> None:
        raise NotImplementedError

    async def call(self, client, name: str, **arguments) -> dict:
        result = await client.call_tool(name, arguments)
        self.assertFalse(result.isError, result.content)
        return result.structuredContent

    async def start_server(self):
        reviews = Reviews(Inbox(self.root / "inbox.sqlite3"))
        return reviews, create_connected_server_and_client_session(self.build(reviews))

    async def drain(self, client, review_id: str) -> None:
        feedback = await self.call(client, "receive_feedback", review_id=review_id)
        while feedback["batch"]:
            await self.call(
                client, "acknowledge_feedback", review_id=review_id,
                batch_id=feedback["batch"]["id"],
            )
            feedback = await self.call(client, "receive_feedback", review_id=review_id)

    async def test_conformance_round_trip(self):
        reviews, connected = await self.start_server()
        try:
            async with connected as client:
                tools = {tool.name for tool in (await client.list_tools()).tools}
                self.assertEqual(tools, TOOLS)
                opened = await self.call(client, "connect_review", **self.connect_arguments)
                review_id = opened["review_id"]
                self.assertTrue(opened["capabilities"]["durable_inbox"])
                await self.drain(client, review_id)
                idle = await self.call(
                    client, "wait_for_feedback", review_id=review_id, author="test-agent",
                    timeout_seconds=1,
                )
                self.assertEqual(idle["status"], "timeout")
                self.assertIsNone(idle["batch"])

                waiting = asyncio.create_task(self.call(
                    client, "wait_for_feedback", review_id=review_id, author="test-agent",
                    timeout_seconds=10,
                ))
                await asyncio.sleep(.2)
                await self.human_comment("Question")
                feedback = await asyncio.wait_for(waiting, 10)
                self.assertEqual(feedback["status"], "feedback")
                event = feedback["batch"]["events"][0]
                self.assertEqual((event["kind"], event["message"]["content"]), ("message", "Question"))
                self.assertIn("comment_type", event["message"])
                replay = await self.call(client, "receive_feedback", review_id=review_id)
                self.assertEqual(replay["batch"]["id"], feedback["batch"]["id"])
        finally:
            await reviews.close()

        # A restarted server replays the unacknowledged batch.
        reviews, connected = await self.start_server()
        try:
            async with connected as client:
                again = await self.call(client, "connect_review", **self.connect_arguments)
                self.assertEqual(again["review_id"], review_id)
                replay = await self.call(client, "receive_feedback", review_id=review_id)
                self.assertEqual(replay["batch"]["id"], feedback["batch"]["id"])
                target = replay["batch"]["events"][0]["message"]
                await self.call(
                    client, "reply", review_id=review_id, message_id=target["id"],
                    message_version=target["version"], content="Answer", author="test-agent",
                )
                current = await self.call(client, "read_review", review_id=review_id)
                self.assertEqual(current["pending"], [])
                await self.call(
                    client, "acknowledge_feedback", review_id=review_id,
                    batch_id=replay["batch"]["id"],
                )
                withdrawn = await self.call(client, "receive_feedback", review_id=review_id)
                self.assertEqual([e["kind"] for e in withdrawn["batch"]["events"]], ["withdrawn"])
                await self.drain(client, review_id)

                await self.close_natively()
                closed = await self.call(
                    client, "wait_for_feedback", review_id=review_id, author="test-agent",
                    timeout_seconds=5,
                )
                self.assertTrue(closed["closed"])
                self.assertEqual([e["kind"] for e in closed["batch"]["events"]], ["closure"])
                self.assertNotIn("approved", closed)
                await self.drain(client, review_id)
                final = await self.call(client, "receive_feedback", review_id=review_id)
                self.assertEqual(final["status"], "closed")
        finally:
            await reviews.close()
