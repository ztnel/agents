#!/usr/bin/env python3
"""Publication gate helpers for reviewed GitHub issue drafts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skillkit.approval import ApprovalError, ApprovalReceipt  # noqa: E402


@dataclass(frozen=True)
class AuthorizationResult:
    authorized: bool
    reasons: list[str]
    reviewed_repo: str | None = None
    reviewed_draft_sha256: str | None = None
    reviewed_metadata_sha256: str | None = None


class PublicationDenied(PermissionError):
    """Raised when a draft has not cleared the publication gate."""


class DuplicateIssueError(ValueError):
    """Raised when a fresh duplicate search finds an open issue."""


def _issue_draft_module():
    existing = sys.modules.get("issue_draft")
    if existing is not None:
        return existing
    path = Path(__file__).resolve().with_name("issue_draft.py")
    spec = importlib.util.spec_from_file_location("issue_draft", path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load issue_draft from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["issue_draft"] = module
    spec.loader.exec_module(module)
    return module


def _normalize_repo(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    owner, sep, name = value.strip().partition("/")
    if not sep or not owner.strip() or not name.strip() or "/" in name:
        raise ValueError(f"{field_name} must be 'owner/repo'")
    return f"{owner.strip()}/{name.strip()}"


def _repo_matches(left: str, right: str) -> bool:
    return left.casefold() == right.casefold()


def _draft_sha256(markdown: str) -> str:
    canonical = markdown.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reviewed_draft_sha256(draft_markdown: str | None) -> str | None:
    if isinstance(draft_markdown, str):
        return _draft_sha256(draft_markdown)
    return None


def authorize_publication(
    report: dict[str, Any] | None,
    *,
    draft_path: str,
    target_repo: str,
    current_head: str,
    draft_markdown: str | None = None,
) -> AuthorizationResult:
    """Authorize publication using a provider-neutral approval receipt."""
    reasons: list[str] = []
    if not isinstance(report, dict):
        return AuthorizationResult(False, ["approval receipt is absent"])

    normalized_target_repo = _normalize_repo(target_repo, field_name="target_repo")
    reviewed_draft_sha256 = _reviewed_draft_sha256(draft_markdown)

    draft = Path(draft_path).resolve()
    workspace = draft.parent
    metadata_hash = None
    if draft.name != "draft.md":
        return AuthorizationResult(False, ["workspace draft must be named draft.md"])
    try:
        receipt = ApprovalReceipt.from_dict(report)
        snapshots = receipt.read_inputs(workspace, current_head, ("draft.md", "metadata.json"))
    except ApprovalError as exc:
        return AuthorizationResult(False, [str(exc)])
    if "draft.md" in snapshots and "metadata.json" in snapshots:
        try:
            raw_metadata = json.loads(snapshots["metadata.json"])
            metadata = validate_metadata(raw_metadata)
            metadata_hash = hashlib.sha256(
                json.dumps(raw_metadata, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if not _repo_matches(metadata["repo"], normalized_target_repo):
                reasons.append("target repo does not match reviewed metadata.repo")
            if (
                draft_markdown is not None
                and _draft_sha256(snapshots["draft.md"].decode("utf-8")) != reviewed_draft_sha256
            ):
                reasons.append("draft markdown does not match the workspace draft")
        except (ValueError, TypeError) as exc:
            reasons.append(f"reviewed workspace inputs are invalid: {exc}")

    return AuthorizationResult(
        not reasons,
        reasons,
        reviewed_repo=normalized_target_repo,
        reviewed_draft_sha256=reviewed_draft_sha256,
        reviewed_metadata_sha256=metadata_hash,
    )


def guard_duplicate_search(matches: list[dict[str, Any]]) -> None:
    """Refuse publication if a fresh duplicate search finds an open issue."""
    for match in matches:
        if str(match.get("state", "")).strip().lower() == "open":
            number = match.get("number", "?")
            raise DuplicateIssueError(f"duplicate issue #{number} is already open")


def _validate_string_list(name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} must be a list of non-blank strings")
    return [item.strip() for item in value]


def validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate MCP issue metadata before publication."""
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a mapping")
    validated: dict[str, Any] = {"repo": _normalize_repo(metadata.get("repo"), field_name="metadata.repo")}

    string_fields = ("issue_type", "milestone", "project", "template")
    list_fields = ("assignees", "labels")
    for field in string_fields:
        if field in metadata:
            value = metadata[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"metadata.{field} must be a non-blank string")
            validated[field] = value.strip()
    for field in list_fields:
        if field in metadata:
            validated[field] = _validate_string_list(f"metadata.{field}", metadata[field])
    return validated


def prepare_issue_payload(
    *,
    authorization: AuthorizationResult,
    draft_markdown: str,
    metadata: dict[str, Any],
    duplicate_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prepare the final payload for the MCP ``issue_write`` call."""
    if not authorization.authorized:
        joined = "; ".join(authorization.reasons) or "publication is not authorized"
        raise PublicationDenied(joined)

    guard_duplicate_search(duplicate_matches)
    validated = validate_metadata(metadata)
    metadata_sha256 = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    issue_draft = _issue_draft_module()
    title, body = issue_draft.publication_sections(draft_markdown)
    if authorization.reviewed_repo is None:
        raise PublicationDenied("authorization is not bound to a reviewed target repo")
    if not _repo_matches(authorization.reviewed_repo, validated["repo"]):
        raise PublicationDenied("metadata.repo does not match the reviewed target repo")
    if metadata_sha256 != authorization.reviewed_metadata_sha256:
        raise PublicationDenied("metadata no longer matches reviewed metadata content")
    draft_sha256 = _draft_sha256(draft_markdown)
    if authorization.reviewed_draft_sha256 is None:
        raise PublicationDenied("authorization is not bound to reviewed draft content")
    if draft_sha256 != authorization.reviewed_draft_sha256:
        raise PublicationDenied("draft markdown no longer matches the reviewed draft content")
    payload = dict(validated)
    payload["title"] = title
    payload["body"] = body
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authorize or prepare a reviewed GitHub issue payload.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    authorize = subparsers.add_parser("authorize", help="Evaluate an approval receipt against current draft inputs.")
    authorize.add_argument("report_file")
    authorize.add_argument("--draft-path", required=True)
    authorize.add_argument(
        "--draft-file",
        help="Optional reviewed draft markdown file. Required if the authorization result must bind reviewed content.",
    )
    authorize.add_argument("--target-repo", required=True)
    authorize.add_argument("--current-head", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="Prepare an issue_write payload from reviewed inputs, keeping the provenance table in body.",
    )
    prepare.add_argument("draft_file")
    prepare.add_argument("metadata_file")
    prepare.add_argument("duplicates_file")
    prepare.add_argument("report_file")
    prepare.add_argument("--draft-path", required=True)
    prepare.add_argument("--target-repo", required=True)
    prepare.add_argument("--current-head", required=True)
    return parser


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "authorize":
        report = _read_json(args.report_file)
        draft_markdown = (
            Path(args.draft_file).read_text(encoding="utf-8")
            if getattr(args, "draft_file", None)
            else None
        )
        authorization = authorize_publication(
            report,
            draft_path=args.draft_path,
            target_repo=args.target_repo,
            current_head=args.current_head,
            draft_markdown=draft_markdown,
        )
        print(
            json.dumps(
                {
                    "authorized": authorization.authorized,
                    "reasons": authorization.reasons,
                    "reviewed_repo": authorization.reviewed_repo,
                    "reviewed_draft_sha256": authorization.reviewed_draft_sha256,
                },
                indent=2,
            )
        )
        return 0 if authorization.authorized else 1
    if args.command == "prepare":
        draft_markdown = Path(args.draft_file).read_text(encoding="utf-8")
        report = _read_json(args.report_file)
        authorization = authorize_publication(
            report,
            draft_path=args.draft_path,
            target_repo=args.target_repo,
            current_head=args.current_head,
            draft_markdown=draft_markdown,
        )
        payload = prepare_issue_payload(
            authorization=authorization,
            draft_markdown=draft_markdown,
            metadata=_read_json(args.metadata_file),
            duplicate_matches=_read_json(args.duplicates_file),
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
