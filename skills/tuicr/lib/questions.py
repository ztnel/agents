"""Explicit question tracking for a generic tuicr approval gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from skillkit import paths, tuicrio
from skillkit.errors import UsageError


def _state_path(repo: str, session: str) -> Path:
    return paths.state_dir("tuicr", "questions", create=True) / (
        paths.short_hash(str(Path(repo).resolve()), session) + ".json"
    )


def _read(repo: str, session: str) -> dict:
    path = _state_path(repo, session)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise UsageError("question registry must be an object")
    for comment_id, binding in value.items():
        if not isinstance(binding, dict) or not isinstance(binding.get("question_sha256"), str):
            raise UsageError(f"invalid question registry entry: {comment_id}")
    return value


def registered_ids(repo: str, session: str) -> set[str]:
    return set(_read(repo, session))


def _comments(repo: str, session: str) -> dict:
    record = tuicrio.session_by_slug(repo, session)
    data = json.loads(Path(record.path).read_text(encoding="utf-8"))
    comments = list(data.get("review_comments", []))
    for file in data.get("files", {}).values():
        comments.extend(file.get("file_comments", []))
        for line_comments in file.get("line_comments", {}).values():
            comments.extend(line_comments)
    return {comment["id"]: comment for comment in comments}


def _digest(comment: dict) -> str:
    return hashlib.sha256(json.dumps(comment, sort_keys=True).encode()).hexdigest()


def register(repo: str, session: str, comment_id: str) -> None:
    comments = _comments(repo, session)
    comment = comments.get(comment_id)
    if not comment or not comment.get("author"):
        raise UsageError("question must name an existing model-authored comment")
    if comment.get("comment_type") in {"description", "review-note"}:
        raise UsageError("orientation comments cannot be registered as questions")
    registry = _read(repo, session)
    digest = _digest(comment)
    if registry.get(comment_id, {}).get("question_sha256") != digest:
        registry[comment_id] = {"question_sha256": digest}
    paths.write_json_atomic(_state_path(repo, session), registry, indent=2)


def resolve(repo: str, session: str, comment_id: str, answer_id: str) -> None:
    registry = _read(repo, session)
    if comment_id not in registry:
        raise UsageError("question is not registered")
    comments = _comments(repo, session)
    question, answer = comments.get(comment_id), comments.get(answer_id)
    if not question or _digest(question) != registry[comment_id]["question_sha256"]:
        raise UsageError("question changed or disappeared; a new review question is required")
    if not answer or answer.get("author") or not answer.get("content", "").strip():
        raise UsageError("answer must name a non-empty human-authored comment")
    if answer["created_at"] <= question["created_at"] or answer_id == comment_id:
        raise UsageError("answer must be newer than the question")
    registry[comment_id].update(answer_id=answer_id, answer_sha256=_digest(answer))
    paths.write_json_atomic(_state_path(repo, session), registry, indent=2)


def unanswered(repo: str, session: str) -> list[dict]:
    registry = _read(repo, session)
    if not registry:
        return []
    comments = _comments(repo, session)
    pending = []
    for comment_id, binding in registry.items():
        question = comments.get(comment_id)
        answer = comments.get(binding.get("answer_id"))
        if (
            question
            and _digest(question) == binding["question_sha256"]
            and answer
            and not answer.get("author")
            and _digest(answer) == binding.get("answer_sha256")
        ):
            continue
        pending.append(question or {"id": comment_id, "content": "Registered question disappeared"})
    return pending
