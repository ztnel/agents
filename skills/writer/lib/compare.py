#!/usr/bin/env python3
"""Measure prose reduction and verify explicitly retained text."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    result = argparse.ArgumentParser(
        description="Compare source and distilled UTF-8 documents."
    )
    result.add_argument("source", type=Path)
    result.add_argument("result", type=Path)
    result.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="TEXT",
        help="Exact text that must remain; repeatable.",
    )
    result.add_argument("--json", action="store_true", dest="as_json")
    return result


def read_text(path: Path) -> str:
    """Read one UTF-8 document."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def compare(source: str, result: str, required: Sequence[str]) -> dict[str, object]:
    """Return deterministic reduction metrics."""
    source_chars = len(source)
    result_chars = len(result)
    removed_chars = source_chars - result_chars
    reduction_percent = (
        round(removed_chars * 100 / source_chars, 1) if source_chars else 0.0
    )
    missing = [text for text in required if text not in result]
    return {
        "source_chars": source_chars,
        "result_chars": result_chars,
        "removed_chars": removed_chars,
        "reduction_percent": reduction_percent,
        "shorter": result_chars < source_chars,
        "missing_required": missing,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the comparison."""
    args = parser().parse_args(argv)
    try:
        metrics = compare(read_text(args.source), read_text(args.result), args.require)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(metrics, indent=2))
    else:
        print(f"source:    {metrics['source_chars']} chars")
        print(f"result:    {metrics['result_chars']} chars")
        print(
            f"reduction: {metrics['removed_chars']} chars "
            f"({metrics['reduction_percent']}%)"
        )
        if metrics["missing_required"]:
            print("missing:   " + ", ".join(repr(item) for item in metrics["missing_required"]))

    if metrics["missing_required"]:
        return 2
    return 0 if metrics["shorter"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
