#!/usr/bin/env python3
"""Report month-to-date AI credit spend against the monthly cap.

Fitting a feature workflow inside a small monthly credit allowance
requires the allowance to be observable. The CLI
records per-request usage in its own session store; this reads that store and
compares the month's total against the cap.

Unit assumption: **1 AIC = 1 AIU = 1e9 ``nano_aiu``**. The store records
``total_nano_aiu``; the mapping from that to the credits a plan is billed in is
not published in the store itself, so it is an assumption rather than a
measured fact. If a billing page disagrees, correct it with ``--cap`` (or the
profile's ``[budget].cap_aic``) rather than by editing this file — the
comparison only needs the two numbers to share a unit.

This is a **reporting** tool, not a gate: it warns and always exits 0 so it can
never block work mid-feature. Deciding what to do about a warning is the
human's call.

Emits ``KEY=VALUE``: ``MONTH``, ``CAP``, ``SPENT``, ``REMAINING``, ``PCT``,
``STATUS``. With ``--by-model``, one
``MODEL_<n>=<name>:<aiu>:<aiu_per_turn>`` line per model, busiest first.

Exit codes:
    0   Always, when the report was produced — including ``over``.
    2   Usage error.
    3   Session store missing or unreadable.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_resolve import DEFAULT_CAP_AIC, load_profile  # noqa: E402
from skillkit.cli import emit, emit_all, run_main, warn  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402

#: Where the CLI records per-request usage.
DEFAULT_STORE = Path.home() / ".copilot" / "session-store.db"

#: nano_aiu per AIU/AIC. See the unit assumption in the module docstring.
NANO_PER_AIC = 1_000_000_000

#: Fraction of the cap at which the report escalates from ``ok`` to ``warn``.
WARN_FRACTION = 0.8


def _month_totals(store: Path, month: str) -> tuple[float, list[tuple[str, float, int]]]:
    """Return ``(total_aic, per_model)`` for *month* (``YYYY-MM``).

    ``per_model`` is ``(model, aic, turns)`` ordered by spend, descending.

    Raises:
        SkillError: Code 3 when the store cannot be read or lacks the usage
            table (an older CLI, or a path that is not a session store).
    """
    if not store.is_file():
        raise SkillError(f"session store not found at {store}", code=3)
    try:
        conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise SkillError(f"cannot open session store {store}: {exc}", code=3) from exc
    try:
        rows = conn.execute(
            "SELECT model, SUM(total_nano_aiu), COUNT(*) "
            "FROM assistant_usage_events "
            "WHERE substr(created_at, 1, 7) = ? "
            "GROUP BY model ORDER BY 2 DESC",
            (month,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise SkillError(
            f"session store {store} has no readable usage table: {exc}", code=3
        ) from exc
    finally:
        conn.close()

    per_model = [
        (model or "(unknown)", (nano or 0) / NANO_PER_AIC, turns)
        for model, nano, turns in rows
    ]
    return sum(aic for _, aic, _ in per_model), per_model


def classify(spent: float, cap: int) -> str:
    """Return ``ok``, ``warn`` or ``over`` for *spent* against *cap*."""
    if spent >= cap:
        return "over"
    if spent >= cap * WARN_FRACTION:
        return "warn"
    return "ok"


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="budget_check.py",
        description="Report month-to-date AI credit spend against the cap.",
    )
    parser.add_argument("--cap", type=int, default=0, help="Monthly cap (default: profile).")
    parser.add_argument("--month", default="", help="YYYY-MM (default: current month, UTC).")
    parser.add_argument("--by-model", action="store_true", help="Break spend down by model.")
    parser.add_argument("--store", default="", help="Session store path (default: standard).")
    parser.add_argument("--repo", default="", help="Repo root searched for .dev.toml.")
    parser.add_argument("--profile", default="", help="Bundled profile name or path.")
    known, extra = parser.parse_known_args(argv)
    if extra:
        raise UsageError(f"unknown arg '{extra[0]}'")

    month = known.month or datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise UsageError(f"--month '{month}' is not YYYY-MM") from exc

    cap = known.cap
    if cap <= 0:
        try:
            cap = load_profile(known.repo or None, known.profile).cap_aic
        except SkillError:
            cap = DEFAULT_CAP_AIC

    store = Path(known.store).expanduser() if known.store else DEFAULT_STORE
    spent, per_model = _month_totals(store, month)
    status = classify(spent, cap)

    emit_all({
        "MONTH": month,
        "CAP": str(cap),
        "SPENT": f"{spent:.1f}",
        "REMAINING": f"{cap - spent:.1f}",
        "PCT": f"{(spent / cap * 100):.1f}" if cap else "0.0",
        "STATUS": status,
    })

    if known.by_model:
        for index, (model, aic, turns) in enumerate(per_model, 1):
            per_turn = aic / turns if turns else 0.0
            emit(f"MODEL_{index}", f"{model}:{aic:.1f}:{per_turn:.2f}")

    if status != "ok":
        warn(
            f"{spent:.1f} of {cap} credits used in {month} "
            f"({spent / cap * 100:.0f}%)." if cap else f"{spent:.1f} credits used."
        )
    return 0


if __name__ == "__main__":
    run_main(main)
