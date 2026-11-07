#!/usr/bin/env python3
"""Presentation feedback MCP server passes the shared conformance suite natively."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path[:0] = [str(LIB), str(Path(__file__).resolve().parents[2] / "_lib")]
import feedback_server  # noqa: E402
import presentation  # noqa: E402
from skillkit.feedback_conformance import Conformance  # noqa: E402


class PresentationConformanceTest(Conformance, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, {
            "XDG_STATE_HOME": str(self.root / "state"), "BROWSER": "true",
        })
        env.start()
        self.addCleanup(env.stop)
        self.doc = self.root / "brief.md"
        self.doc.write_text("# Review\n\nExplain this.\n")
        opened = json.loads(subprocess.run(
            [sys.executable, str(LIB / "presentation.py"), "open", str(self.doc), "--no-browser"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout)
        self.addCleanup(presentation.proc.terminate, opened["pid"])
        self.native = feedback_server.Presentation(self.doc)
        self.connect_arguments = {"doc": str(self.doc)}

    def build(self, reviews):
        return feedback_server.build(reviews)

    async def human_comment(self, content):
        await self.native.call("/api/comment", {"start": 3, "end": 3, "content": content})

    async def close_natively(self):
        await self.native.call("/api/close", {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
