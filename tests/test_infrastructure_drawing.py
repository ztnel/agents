#!/usr/bin/env python3
"""Keep the source-controlled drawing's owned component inventory current."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InfrastructureDrawingTest(unittest.TestCase):
    def test_owned_inventory_matches_manifests_and_skill_directories(self):
        expected = {
            "skill_" + path.name.replace("-", "_")
            for path in (ROOT / "skills").iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        }
        for path in (ROOT / "com.github.copilot/agents").glob("*.agent.md"):
            name = re.search(r"^name:\s*(\S+)", path.read_text(), re.MULTILINE)
            self.assertIsNotNone(name, str(path))
            expected.add("agent_" + name[1].replace("-", "_"))
        servers = json.loads((ROOT / "mcp.json").read_text())["mcpServers"]
        expected.update("mcp_" + name.replace("-", "_") for name in servers)
        drawing = (ROOT / "docs/INFRASTRUCTURE.md").read_text()
        actual = set(re.findall(r"^\s*((?:skill|agent|mcp)_\w+)\[", drawing, re.MULTILINE))
        self.assertEqual(
            actual, expected,
            f"Update docs/INFRASTRUCTURE.md: missing={expected-actual}, stale={actual-expected}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
