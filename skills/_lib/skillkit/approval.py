"""Provider-neutral, versioned approval receipts for reviewed workspace files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping

from .gitio import git


SCHEMA = "agents.approval/v1"


class ApprovalError(ValueError):
    """An approval receipt is invalid or no longer covers current inputs."""


@dataclass(frozen=True)
class ReviewedFile:
    path: str
    content_sha256: str


@dataclass(frozen=True)
class ApprovalReceipt:
    workspace: str
    head: str
    approved: bool
    files: tuple[ReviewedFile, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "workspace": self.workspace,
            "head": self.head,
            "approved": self.approved,
            "files": [
                {"path": file.path, "content_sha256": file.content_sha256}
                for file in self.files
            ],
        }

    @classmethod
    def from_dict(cls, value: Any) -> ApprovalReceipt:
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            raise ApprovalError("unsupported or missing approval receipt schema")
        if not isinstance(value.get("approved"), bool):
            raise ApprovalError("approval receipt must declare a boolean approved value")
        workspace, head = value.get("workspace"), value.get("head")
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            raise ApprovalError("approval workspace must be an absolute path")
        if not isinstance(head, str) or not head.strip():
            raise ApprovalError("approval receipt must declare a HEAD")
        entries = value.get("files")
        if not isinstance(entries, list):
            raise ApprovalError("approval files must be a list")
        files = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ApprovalError("approval file must be an object")
            path, digest = entry.get("path"), entry.get("content_sha256")
            if (
                not isinstance(path, str) or not path or Path(path).is_absolute()
                or ".." in Path(path).parts or path in seen
            ):
                raise ApprovalError("approval file paths must be unique workspace-relative paths")
            if (
                not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ApprovalError("approval file must have a SHA-256 content fingerprint")
            seen.add(path)
            files.append(ReviewedFile(path, digest))
        return cls(workspace, head, value["approved"], tuple(files))

    def read_inputs(self, workspace: Path, head: str, required: tuple[str, ...]) -> Mapping[str, bytes]:
        """Validate current workspace identity and return fingerprint-verified inputs."""
        root = workspace.resolve()
        if not self.approved:
            raise ApprovalError("approval gate did not approve the review")
        if Path(self.workspace).resolve() != root:
            raise ApprovalError("approval workspace does not match the current workspace")
        current = git(str(root), "rev-parse", "HEAD")
        if not current.ok or current.stdout.strip() != head or self.head != head:
            raise ApprovalError("approval HEAD no longer matches current workspace HEAD")
        files = {file.path: file.content_sha256 for file in self.files}
        snapshots = {}
        for name in required:
            if name not in files:
                raise ApprovalError(f"approval does not include reviewed {name}")
            target = root / name
            try:
                target.resolve().relative_to(root)
            except ValueError as exc:
                raise ApprovalError(f"reviewed {name} resolves outside the workspace") from exc
            try:
                content = target.read_bytes()
            except OSError as exc:
                raise ApprovalError(f"reviewed {name} cannot be read: {exc}") from exc
            if hashlib.sha256(content).hexdigest() != files[name]:
                raise ApprovalError(f"{name} no longer matches reviewed content")
            snapshots[name] = content
        return snapshots
