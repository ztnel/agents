#!/usr/bin/env python3
"""Exercise the moved entry point and preservation of saved conversations."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import presentation  # noqa: E402


class PresentationCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        env = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.directory / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.doc = (self.directory / "brief.md").resolve()
        self.doc.write_text("# Alignment\n\nProposed outcome.\n")
        self.entry = Path(presentation.__file__)

    def cli(self, *args):
        result = subprocess.run(
            [sys.executable, str(self.entry), *args],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def open(self):
        opened = self.cli("open", str(self.doc), "--no-browser")
        self.addCleanup(presentation.proc.terminate, opened["pid"])
        self.assertTrue(presentation.healthy(presentation.load(self.doc)))
        return opened

    def test_tab_opens_only_once_the_agent_waits(self):
        opened_log = self.directory / "opened.txt"
        browser = self.directory / "browser.sh"
        browser.write_text(f'#!/bin/sh\necho "$1" >> "{opened_log}"\n')
        browser.chmod(0o755)
        with patch.dict(os.environ, {"BROWSER": str(browser)}):
            opened = self.cli("open", str(self.doc))
            self.addCleanup(presentation.proc.terminate, opened["pid"])
            self.assertEqual(opened["browser"], "opens when you wait")
            self.assertFalse(opened_log.exists(), "no tab before an agent listens")
            for _ in range(2):
                self.cli("wait", str(self.doc), "--model", "test-model", "--timeout", "1")
        self.assertEqual(opened_log.read_text().split(), [opened["url"]])

    def test_moved_cli_reuses_and_reopens_saved_conversation(self):
        opened = self.open()
        reused = self.cli("open", str(self.doc), "--no-browser")
        self.assertTrue(reused["reused"])
        self.assertEqual(opened["url"], reused["url"])

        meta = presentation.load(self.doc)
        posted = presentation.call(meta, "/api/comment", {
            "start": 3, "end": 3, "content": "Clarify the outcome.",
        })
        feedback = self.cli("wait", str(self.doc), "--model", "test-model", "--timeout", "1")
        self.assertEqual(feedback["status"], "feedback")
        self.assertEqual(feedback["pending"][0]["context"], "Proposed outcome.")
        self.cli("reply", str(self.doc), "--model", "test-model",
                 "--thread", posted["thread"], "--body", "This is a proposal, not approval.")
        self.assertEqual(self.cli("read", str(self.doc))["pending"], [])

        store = presentation.session_dir(self.doc) / "threads.json"
        self.assertIn("demystify", store.parts)
        self.cli("close", str(self.doc))
        self.assertTrue(json.loads(store.read_text())["closed"])
        presentation.proc.terminate(opened["pid"])
        closed = self.cli("wait", str(self.doc), "--model", "test-model", "--timeout", "1")
        self.assertEqual(closed["status"], "closed")

        self.open()
        resumed = self.cli("read", str(self.doc))
        self.assertEqual(resumed["threads"][0]["id"], posted["thread"])
        self.assertEqual(len(resumed["threads"][0]["messages"]), 2)
        self.cli("close", str(self.doc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
