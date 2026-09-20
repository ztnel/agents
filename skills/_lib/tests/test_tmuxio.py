#!/usr/bin/env python3
"""Regression tests for tmux pane detection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillkit import tmuxio


class PaneExistsTest(unittest.TestCase):
    """A stale target may succeed while returning no pane identity."""

    def test_nonempty_pane_id_exists(self) -> None:
        with patch.object(tmuxio, "display", return_value="%81"):
            self.assertTrue(tmuxio.pane_exists("%81"))

    def test_empty_success_is_not_a_pane(self) -> None:
        with patch.object(tmuxio, "display", return_value=""):
            self.assertFalse(tmuxio.pane_exists("%81"))

    def test_failed_lookup_is_not_a_pane(self) -> None:
        with patch.object(tmuxio, "display", return_value=None):
            self.assertFalse(tmuxio.pane_exists("%81"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
