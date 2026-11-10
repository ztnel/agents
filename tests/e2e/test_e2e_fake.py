#!/usr/bin/env python3
"""Protocol tier: the full review workflow with a scripted agent and a scripted human."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MISSING = [tool for tool in ("tmux", "tuicr", "uvx") if shutil.which(tool) is None]


class FakeAgentEndToEnd(unittest.TestCase):
    def test_slugify_scenario(self):
        if MISSING:
            if os.environ.get("CI"):
                self.fail(f"CI must install {MISSING}")
            self.skipTest(f"needs {MISSING}")
        argv = [sys.executable, str(HERE / "run_e2e.py"), "--agent", "fake"]
        if os.environ.get("E2E_ARTIFACTS"):
            argv += ["--artifacts", os.environ["E2E_ARTIFACTS"]]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        self.assertIn("E2E_OK", result.stdout, result.stdout[-4000:] + result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
