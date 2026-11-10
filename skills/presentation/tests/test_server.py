#!/usr/bin/env python3
"""HTTP contract between the page, the server and waiting agents."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import server  # noqa: E402


class ServerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.doc = self.dir / "doc.md"
        self.doc.write_text("# T\n\nLine one.\nLine two.\n\n```svg\n<svg><g data-label=\"sw\"><text>Switch node</text></g></svg>\n```\n")
        self.srv = server.serve(self.doc, self.dir / "threads.json", "tok")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, route, body=None, token="tok"):
        request = urllib.request.Request(
            f"{self.srv.origin}{route}", data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Token": token, "Content-Type": "application/json"},
            method="POST" if body is not None else "GET")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def wait_async(self, timeout=10):
        result = {}
        thread = threading.Thread(target=lambda: result.update(
            self.call(f"/agent/wait?model=m-1&timeout={timeout}")))
        thread.start()
        deadline = time.monotonic() + 5
        while not self.srv.session.waiters and time.monotonic() < deadline:
            time.sleep(0.02)
        return thread, result

    def test_token_required(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call("/agent/read", token="wrong")
        self.assertEqual(ctx.exception.code, 403)

    def test_waiting_agent_is_shown_and_receives_comment_until_answered(self):
        thread, result = self.wait_async()
        self.assertEqual(self.call("/api/state")["agent"], {"state": "waiting", "models": ["m-1"]})
        posted = self.call("/api/comment", {"start": 3, "end": 4, "content": "why?"})
        thread.join(5)
        self.assertEqual(result["status"], "feedback")
        pending = result["pending"][0]
        self.assertEqual(pending["context"], "Line one.\nLine two.")
        self.assertEqual(self.call("/api/state")["agent"]["state"], "working")
        self.call("/agent/post", {"thread": posted["thread"], "model": "m-1", "content": "because"})
        self.assertEqual(self.call("/agent/read")["pending"], [])
        self.assertEqual(self.call("/agent/wait?model=m-1&timeout=1")["status"], "timeout")

    def test_unattached_server_exits_without_closing_the_session(self):
        srv = server.serve(self.doc, self.dir / "idle.json", "tok", idle=0.4)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(srv.server_close)
        request = urllib.request.Request(
            f"{srv.origin}/agent/wait?model=m-1&timeout=1", headers={"X-Token": "tok"})
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(json.loads(response.read())["status"], "timeout")
        self.assertTrue(thread.is_alive(), "a request in flight keeps the server up")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(srv.session.closed)

    def test_close_ends_waits_and_persists(self):
        thread, result = self.wait_async()
        self.call("/api/close", {})
        thread.join(5)
        self.assertEqual(result["status"], "closed")
        self.assertTrue(json.loads((self.dir / "threads.json").read_text())["closed"])

    def test_agent_reply_requires_model(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call("/agent/post", {"document": True, "content": "hi"})
        self.assertEqual(ctx.exception.code, 400)

    def test_bad_source_revision_keeps_last_page_and_recovers(self):
        before = self.call("/api/state")["html"]
        self.doc.write_text("Claim [^1].\n\n[^1]: First\n[^1]: Duplicate\n")
        self.srv.session.mtime = None
        self.srv.session.reload()
        state = self.call("/api/state")
        self.assertIn("duplicate source definition", state["error"])
        self.assertEqual(state["html"], before)
        self.doc.write_text("Claim [^1].\n\n[^1]: Correct source\n")
        self.srv.session.reload()
        state = self.call("/api/state")
        self.assertEqual(state["error"], "")
        self.assertIn('id="source-1"', state["html"])

    def test_excerpt_anchor_must_match_selected_lines(self):
        self.call("/api/comment", {"start": 3, "end": 4, "excerpt": "one.  Line", "col": 5, "content": "?"})
        pending = self.call("/agent/read")["pending"][0]
        self.assertIn('(selected text: "one. Line")', pending["context"])
        for bad in ({"excerpt": "Line three"}, {"excerpt": "one", "element": "x"}):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.call("/api/comment", {"start": 3, "end": 4, "content": "?", **bad})
            self.assertEqual(ctx.exception.code, 400)

    def test_excerpt_can_select_diagram_text_within_its_element(self):
        self.call("/api/comment", {"start": 6, "end": 6, "excerpt": "node", "element": "sw", "content": "?"})
        context = self.call("/agent/read")["pending"][0]["context"]
        self.assertIn("(diagram element: sw)", context)
        self.assertIn('(selected text: "node")', context)


    def test_math_comment_context_includes_latex_source(self):
        self.doc.write_text("# T\n\n$$\n\\frac{a}{b}\n$$\n")
        self.srv.session.mtime = None
        self.srv.session.reload()
        self.call("/api/comment", {"start": 3, "end": 3, "content": "?"})
        context = self.call("/agent/read")["pending"][0]["context"]
        self.assertIn("(LaTeX source, line 3: \\frac{a}{b})", context)

    def test_widget_is_served_sandboxed_and_reports_state(self):
        self.doc.write_text("# T\n\n```widget Duty\n<input id=\"D\"><script>go()</script>\n```\n")
        self.srv.session.mtime = None
        self.srv.session.reload()
        key = next(iter(self.srv.session.rendered.widgets))
        with urllib.request.urlopen(f"{self.srv.origin}/widget/{key}", timeout=10) as response:
            csp = response.headers["Content-Security-Policy"]
            self.assertIn("<script>go()</script>", response.read().decode())
        self.assertIn("sandbox allow-scripts", csp)
        self.assertNotIn("connect-src", csp)  # default-src 'none': no network from widget code
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{self.srv.origin}/widget/0000", timeout=10)
        self.call("/api/comment", {"start": 3, "end": 3, "state": {"D": "0.4", "on": True}, "content": "?"})
        self.assertIn("(widget state when commented: D=0.4, on=true)", self.call("/agent/read")["pending"][0]["context"])
        with self.assertRaises(urllib.error.HTTPError):
            self.call("/api/comment", {"start": 1, "end": 1, "state": {"D": 1}, "content": "?"})


if __name__ == "__main__":
    unittest.main()
