#!/usr/bin/env python3
"""Pin the root ``mcp.json`` Agent Plugins 1.0 contract for the GitHub MCP server.

Structural checks only (stdlib ``json``, no network): a streamable-http server
named for GitHub, pointed at the official Copilot MCP endpoint, scoped to
exactly the ``repos`` and ``issues`` toolsets, and carrying no credentials.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

MCP_JSON = Path(__file__).resolve().parents[3] / "mcp.json"

EXPECTED_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
EXPECTED_URL = "https://api.githubcopilot.com/mcp/"
EXPECTED_TOOLSETS = "repos,issues"

#: Property names that must never appear anywhere in the document: a
#: streamable-http server authenticates via the platform, not an embedded
#: secret.
CREDENTIAL_KEYS = {
    "authorization",
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "access_token",
    "client_secret",
}


def _find_credential_keys(node: object, found: set[str]) -> None:
    """Recursively collect any disallowed credential-shaped keys in *node*."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower().replace("-", "_") in CREDENTIAL_KEYS:
                found.add(key)
            _find_credential_keys(value, found)
    elif isinstance(node, list):
        for item in node:
            _find_credential_keys(item, found)


class McpConfigContractTest(unittest.TestCase):
    """``mcp.json`` at the repo root must declare the GitHub MCP server."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.exists = MCP_JSON.exists()
        cls.data = None
        if cls.exists:
            with MCP_JSON.open(encoding="utf-8") as stream:
                cls.data = json.load(stream)

    def test_mcp_json_exists_at_repo_root(self) -> None:
        self.assertTrue(self.exists, f"expected {MCP_JSON} to exist")

    def test_declares_the_agent_plugins_schema(self) -> None:
        self.assertIsNotNone(self.data, "mcp.json is missing or unparseable")
        self.assertEqual(self.data.get("$schema"), EXPECTED_SCHEMA)

    def test_has_exactly_one_github_mcp_server(self) -> None:
        self.assertIsNotNone(self.data, "mcp.json is missing or unparseable")
        servers = self.data.get("mcpServers", {})
        self.assertIsInstance(servers, dict)
        github_servers = {
            name: cfg
            for name, cfg in servers.items()
            if isinstance(cfg, dict) and cfg.get("url") == EXPECTED_URL
        }
        self.assertEqual(
            len(github_servers),
            1,
            f"expected exactly one server pointed at {EXPECTED_URL}, found {list(servers)}",
        )

    def test_server_is_streamable_http_to_the_official_endpoint(self) -> None:
        self.assertIsNotNone(self.data, "mcp.json is missing or unparseable")
        servers = self.data.get("mcpServers", {})
        server = next(
            (cfg for cfg in servers.values() if isinstance(cfg, dict) and cfg.get("url") == EXPECTED_URL),
            None,
        )
        self.assertIsNotNone(server, "no server configured for the GitHub MCP endpoint")
        self.assertEqual(server.get("type"), "streamable-http")
        self.assertEqual(server.get("url"), EXPECTED_URL)

    def test_toolsets_are_exactly_repos_and_issues(self) -> None:
        self.assertIsNotNone(self.data, "mcp.json is missing or unparseable")
        servers = self.data.get("mcpServers", {})
        server = next(
            (cfg for cfg in servers.values() if isinstance(cfg, dict) and cfg.get("url") == EXPECTED_URL),
            None,
        )
        self.assertIsNotNone(server, "no server configured for the GitHub MCP endpoint")
        headers = server.get("headers", {})
        self.assertIn("X-MCP-Toolsets", headers)
        requested = {part.strip() for part in headers["X-MCP-Toolsets"].split(",") if part.strip()}
        self.assertEqual(requested, {"repos", "issues"})
        # Pin the exact serialized value too: no extra whitespace or reordering
        # that would silently widen the granted scope.
        self.assertEqual(headers["X-MCP-Toolsets"], EXPECTED_TOOLSETS)

    def test_no_embedded_credentials_or_secrets(self) -> None:
        self.assertIsNotNone(self.data, "mcp.json is missing or unparseable")
        found: set[str] = set()
        _find_credential_keys(self.data, found)
        self.assertEqual(found, set(), f"mcp.json must not embed credential-shaped keys: {found}")

    def test_document_has_no_additional_top_level_properties(self) -> None:
        self.assertIsNotNone(self.data, "mcp.json is missing or unparseable")
        self.assertEqual(set(self.data.keys()), {"$schema", "mcpServers"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
