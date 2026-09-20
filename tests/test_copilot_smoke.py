#!/usr/bin/env python3
"""Self-checks for the Copilot smoke helper."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().with_name("copilot_smoke.py")
spec = importlib.util.spec_from_file_location("copilot_smoke", MODULE)
copilot_smoke = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(copilot_smoke)


class TransportAliasTest(unittest.TestCase):
    def test_streamable_http_maps_to_runtime_http(self) -> None:
        self.assertTrue(copilot_smoke._transport_matches("streamable-http", "http"))

    def test_stdio_remains_exact(self) -> None:
        self.assertTrue(copilot_smoke._transport_matches("stdio", "stdio"))
        self.assertFalse(copilot_smoke._transport_matches("stdio", "http"))

    def test_sse_remains_exact(self) -> None:
        self.assertTrue(copilot_smoke._transport_matches("sse", "sse"))
        self.assertFalse(copilot_smoke._transport_matches("sse", "http"))

    def test_agent_command_uses_no_tools_no_questions_flags(self) -> None:
        argv = copilot_smoke._agent_argv("developer", plugin_dir=None)
        self.assertIn("--available-tools", argv)
        self.assertIn("", argv)
        self.assertIn("--no-ask-user", argv)
        self.assertIn("--silent", argv)
        self.assertIn("--secret-env-vars=COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN", argv)
        self.assertNotIn("--allow-all", argv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
