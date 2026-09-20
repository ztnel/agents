#!/usr/bin/env python3
"""Draft format helpers for reviewed GitHub issue markdown."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import re
from pathlib import Path
from typing import Iterable

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MODEL_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
_PLACEHOLDER_MODELS = {"copilot-auto", "auto", "unknown"}
_PROVENANCE_HEADING = "## Provenance"
_PROVENANCE_LABELS = ("Identified by", "Authored by", "Approved by")


class DraftFormatError(ValueError):
    """Raised when draft markdown does not match the published format."""


class ProvenanceError(ValueError):
    """Raised when provenance identities are missing or malformed."""


@dataclass(frozen=True)
class Provenance:
    identified_by: str
    authored_by: tuple[str, ...]
    approved_by: str


@dataclass(frozen=True)
class Draft:
    title: str
    body: str
    provenance: Provenance


def _require_email(value: str, field_name: str) -> str:
    email = value.strip()
    if not email:
        raise ProvenanceError(f"{field_name} is required")
    if not _EMAIL_RE.match(email):
        raise ProvenanceError(f"{field_name} must be a valid email address")
    return email


def _require_model_id(value: str, field_name: str) -> str:
    model_id = value.strip()
    if not model_id:
        raise ProvenanceError(f"{field_name} is required")
    if model_id.casefold() in _PLACEHOLDER_MODELS:
        raise ProvenanceError(f"placeholder model ID is not allowed: {model_id}")
    if "@" in model_id:
        raise ProvenanceError(f"{field_name} must be a concrete model ID, not an email")
    if not _MODEL_ID_RE.fullmatch(model_id) or not any(char.isdigit() for char in model_id):
        raise ProvenanceError(
            f"{field_name} must be a concrete model ID like 'gpt-5.1' or 'claude-sonnet-5'"
        )
    return model_id


def _require_identified_by(value: str) -> str:
    identified_by = value.strip()
    if not identified_by:
        raise ProvenanceError("identified_by is required")
    if identified_by.casefold() in _PLACEHOLDER_MODELS:
        raise ProvenanceError(f"placeholder model ID is not allowed: {identified_by}")
    if "@" in identified_by:
        return _require_email(identified_by, "identified_by")
    if _EMAIL_RE.match(identified_by):
        return _require_email(identified_by, "identified_by")
    return _require_model_id(identified_by, "identified_by")


def _normalize_authors(authored_by: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in authored_by:
        model_id = _require_model_id(raw, "authored_by")
        if model_id in seen:
            continue
        seen.add(model_id)
        normalized.append(model_id)
    if not normalized:
        raise ProvenanceError("at least one concrete author model ID is required")
    return tuple(normalized)


def _normalize_body(body: str) -> str:
    text = body.replace("\r\n", "\n").strip("\n")
    if not text.strip():
        raise DraftFormatError("draft body must not be blank")
    return text + "\n"


def _normalize_title(title: str) -> str:
    clean = title.strip()
    if not clean:
        raise DraftFormatError("draft title must not be blank")
    if clean.startswith("#"):
        clean = clean.lstrip("#").strip()
    if not clean:
        raise DraftFormatError("draft title must not be blank")
    return clean


def render_draft(
    *,
    title: str,
    body: str,
    identified_by: str,
    authored_by: Iterable[str],
    approved_by: str,
) -> str:
    """Render a validated issue draft as markdown."""
    clean_title = _normalize_title(title)
    clean_body = _normalize_body(body)
    provenance = Provenance(
        identified_by=_require_identified_by(identified_by),
        authored_by=_normalize_authors(authored_by),
        approved_by=_require_email(approved_by, "approved_by"),
    )
    author_text = ", ".join(provenance.authored_by)
    return (
        f"# {clean_title}\n\n"
        f"{clean_body}\n"
        f"{_PROVENANCE_HEADING}\n\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Identified by | {provenance.identified_by} |\n"
        f"| Authored by | {author_text} |\n"
        f"| Approved by | {provenance.approved_by} |\n"
    )


def _split_draft(markdown: str) -> tuple[str, str, str]:
    text = markdown.replace("\r\n", "\n")
    if not text.startswith("# "):
        raise DraftFormatError("draft must start with an H1 title")
    title_line, sep, _rest = text.partition("\n")
    if not sep:
        raise DraftFormatError("draft body is missing")
    heading_marker = f"\n{_PROVENANCE_HEADING}\n"
    index = text.rfind(heading_marker)
    if index == -1:
        raise DraftFormatError("draft is missing the provenance section")
    title = _normalize_title(title_line[2:])
    body_part = text[len(title_line) + 1:index]
    body = _normalize_body(body_part)
    provenance_section = text[index + 1 :].strip() + "\n"
    return title, body, provenance_section


def _parse_table_row(line: str) -> tuple[str, str]:
    if not line.startswith("|") or not line.endswith("|"):
        raise DraftFormatError("provenance rows must be markdown table rows")
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    if len(cells) != 2:
        raise DraftFormatError("provenance rows must have exactly two cells")
    return cells[0], cells[1]


def _parse_provenance(section: str) -> Provenance:
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    if len(lines) < 6 or lines[0] != _PROVENANCE_HEADING:
        raise DraftFormatError("provenance section is malformed")
    header_label, header_value = _parse_table_row(lines[1])
    if (header_label, header_value) != ("Field", "Value"):
        raise DraftFormatError("provenance table header must be '| Field | Value |'")
    if set(lines[2].replace("|", "").strip()) - {"-", ":"}:
        raise DraftFormatError("provenance table separator is malformed")
    parsed: dict[str, str] = {}
    for line in lines[3:]:
        label, value = _parse_table_row(line)
        parsed[label] = value
    missing = [label for label in _PROVENANCE_LABELS if label not in parsed]
    if missing:
        raise DraftFormatError(f"provenance table is missing: {', '.join(missing)}")
    return Provenance(
        identified_by=_require_identified_by(parsed["Identified by"]),
        authored_by=_normalize_authors(part.strip() for part in parsed["Authored by"].split(",")),
        approved_by=_require_email(parsed["Approved by"], "approved_by"),
    )


def parse_draft(markdown: str) -> Draft:
    """Parse and validate a rendered issue draft."""
    title, body, provenance_section = _split_draft(markdown)
    provenance = _parse_provenance(provenance_section)
    return Draft(title=title, body=body, provenance=provenance)


def strip_provenance(markdown: str) -> str:
    """Return the draft without its final provenance section."""
    parsed = parse_draft(markdown)
    return f"# {parsed.title}\n\n{parsed.body}"


def publication_sections(markdown: str) -> tuple[str, str]:
    """Return validated ``(title, body_with_provenance)`` for publication."""
    title, body, provenance_section = _split_draft(markdown)
    _parse_provenance(provenance_section)
    return title, f"{body}\n{provenance_section}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render, validate, or strip a GitHub issue draft.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="Render markdown from structured fields.")
    render.add_argument("--title", required=True)
    render.add_argument("--body-file", required=True)
    render.add_argument("--identified-by", required=True, help="Human email or concrete model ID.")
    render.add_argument("--authored-by", action="append", required=True, help="Concrete model ID.")
    render.add_argument("--approved-by", required=True, help="Human email.")

    validate = subparsers.add_parser("validate", help="Validate an existing draft file.")
    validate.add_argument("draft")

    strip_cmd = subparsers.add_parser("strip", help="Print the draft without its provenance section.")
    strip_cmd.add_argument("draft")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "render":
        body = Path(args.body_file).read_text(encoding="utf-8")
        print(
            render_draft(
                title=args.title,
                body=body,
                identified_by=args.identified_by,
                authored_by=args.authored_by,
                approved_by=args.approved_by,
            ),
            end="",
        )
        return 0

    markdown = Path(args.draft).read_text(encoding="utf-8")
    if args.command == "validate":
        draft = parse_draft(markdown)
        print(
            json.dumps(
                {
                    "title": draft.title,
                    "body": draft.body,
                    "identified_by": draft.provenance.identified_by,
                    "authored_by": list(draft.provenance.authored_by),
                    "approved_by": draft.provenance.approved_by,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "strip":
        print(strip_provenance(markdown), end="")
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
