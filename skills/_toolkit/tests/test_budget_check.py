#!/usr/bin/env python3
"""Tests for budget_check.py — cap math against a synthetic usage store."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
sys.path.insert(0, str(LIB))

from budget_check import NANO_PER_AIC, classify  # noqa: E402
from skillkit.cli import parse_metadata  # noqa: E402


def make_store(path: Path, rows: list[tuple[str, str, float]]) -> None:
    """Write a minimal usage store. Rows are ``(created_at, model, aic)``."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE assistant_usage_events "
        "(created_at TEXT, model TEXT, total_nano_aiu INTEGER)"
    )
    conn.executemany(
        "INSERT INTO assistant_usage_events VALUES (?, ?, ?)",
        [(when, model, int(aic * NANO_PER_AIC)) for when, model, aic in rows],
    )
    conn.commit()
    conn.close()


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the entry point as a subprocess."""
    return subprocess.run(
        [sys.executable, str(LIB / "budget_check.py"), *args],
        capture_output=True, text=True,
    )


def report(store: Path, *args: str) -> tuple[dict[str, str], subprocess.CompletedProcess]:
    """Run against *store* with a fixed cap and month unless overridden."""
    result = run_cli("--store", str(store), *args)
    return parse_metadata(result.stdout), result


class TestClassify(unittest.TestCase):
    """Boundaries are exact so a report never straddles two statuses."""

    def test_below_warn(self) -> None:
        self.assertEqual(classify(5599.0, 7000), "ok")

    def test_warn_boundary_is_inclusive(self) -> None:
        self.assertEqual(classify(5600.0, 7000), "warn")

    def test_over_boundary_is_inclusive(self) -> None:
        self.assertEqual(classify(7000.0, 7000), "over")

    def test_above_cap(self) -> None:
        self.assertEqual(classify(9000.0, 7000), "over")


class TestMonthFiltering(unittest.TestCase):
    """Only the requested month counts."""

    ROWS = [
        ("2025-02-28T23:00:00Z", "model-a", 100.0),
        ("2025-03-01T00:00:00Z", "model-a", 250.0),
        ("2025-03-31T23:59:59Z", "model-b", 150.0),
        ("2025-04-01T00:00:00Z", "model-a", 999.0),
    ]

    def test_sums_only_that_month(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            make_store(store, self.ROWS)
            meta, result = report(store, "--month", "2025-03", "--cap", "7000")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(meta["SPENT"], "400.0")
            self.assertEqual(meta["MONTH"], "2025-03")
            self.assertEqual(meta["REMAINING"], "6600.0")
            self.assertEqual(meta["STATUS"], "ok")

    def test_empty_month_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            make_store(store, self.ROWS)
            meta, _ = report(store, "--month", "2025-01", "--cap", "7000")
            self.assertEqual(meta["SPENT"], "0.0")
            self.assertEqual(meta["PCT"], "0.0")


class TestWarnOnly(unittest.TestCase):
    """Over budget must never block the workflow."""

    def test_over_still_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            make_store(store, [("2025-03-02T00:00:00Z", "m", 9000.0)])
            meta, result = report(store, "--month", "2025-03", "--cap", "7000")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(meta["STATUS"], "over")
            self.assertEqual(meta["REMAINING"], "-2000.0")
            self.assertIn("WARN:", result.stderr)

    def test_ok_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            make_store(store, [("2025-03-02T00:00:00Z", "m", 10.0)])
            _, result = report(store, "--month", "2025-03", "--cap", "7000")
            self.assertNotIn("WARN:", result.stderr)


class TestByModel(unittest.TestCase):
    """The per-model breakdown is what makes model choice a decision."""

    def test_ordered_and_shaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            make_store(store, [
                ("2025-03-02T00:00:00Z", "cheap", 10.0),
                ("2025-03-02T00:00:00Z", "cheap", 10.0),
                ("2025-03-03T00:00:00Z", "dear", 100.0),
            ])
            meta, _ = report(store, "--month", "2025-03", "--cap", "7000", "--by-model")
            self.assertEqual(meta["MODEL_1"], "dear:100.0:100.00")
            self.assertEqual(meta["MODEL_2"], "cheap:20.0:10.00")

    def test_absent_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            make_store(store, [("2025-03-02T00:00:00Z", "m", 1.0)])
            meta, _ = report(store, "--month", "2025-03", "--cap", "7000")
            self.assertNotIn("MODEL_1", meta)


class TestProfileCap(unittest.TestCase):
    """The cap comes from the profile when not given explicitly."""

    def test_default_profile_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            make_store(store, [("2025-03-02T00:00:00Z", "m", 1.0)])
            meta, _ = report(store, "--month", "2025-03", "--profile", "default")
            self.assertEqual(meta["CAP"], "7000")

    def test_repo_profile_cap_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".dev.toml").write_text(
                'name = "small"\n'
                "[branch]\npattern = '^(?P<ticket>[A-Z]+-[0-9]+)-(?P<slug>[a-z]+)$'\n"
                '[ticket]\nurl = "https://t/{ticket}"\n'
                "[budget]\ncap_aic = 500\n",
                encoding="utf-8",
            )
            store = repo / "s.db"
            make_store(store, [("2025-03-02T00:00:00Z", "m", 450.0)])
            meta, _ = report(store, "--month", "2025-03", "--repo", str(repo))
            self.assertEqual(meta["CAP"], "500")
            self.assertEqual(meta["STATUS"], "warn")


class TestErrors(unittest.TestCase):
    """Failure modes stay distinguishable by exit code."""

    def test_missing_store_exits_3(self) -> None:
        self.assertEqual(run_cli("--store", "/nonexistent/x.db").returncode, 3)

    def test_store_without_usage_table_exits_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "s.db"
            sqlite3.connect(store).close()
            self.assertEqual(run_cli("--store", str(store)).returncode, 3)

    def test_bad_month_exits_2(self) -> None:
        self.assertEqual(run_cli("--month", "March").returncode, 2)

    def test_unknown_arg_exits_2(self) -> None:
        self.assertEqual(run_cli("--nope").returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
