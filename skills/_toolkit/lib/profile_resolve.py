#!/usr/bin/env python3
"""Resolve and validate the dev platform profile.

Everything job-specific about the workflow — how branches are named, where
tickets live, which PR template applies, what the monthly credit cap is — comes
from one TOML profile so the skill itself stays vendor-neutral. This module is
both a CLI entry point and the importable helper the other dev scripts use
(``load_profile``), so profile lookup and validation exist exactly once.

Lookup order (first hit wins):

1. ``<repo>/.dev.toml``
2. ``~/.config/dev/profile.toml``
3. ``<skill>/templates/profiles/default.toml``

``--profile`` overrides the search with either a bundled profile name (e.g.
``example-github``) or an explicit path.

Command safety: ``[ticket].fetch`` and ``[pr].open`` are argv **lists**, never
shell strings. :func:`render_command` substitutes placeholders per element and
expands ``~`` per element; callers spawn them with ``skillkit.proc.run``, which
never uses a shell. A ticket id or PR title therefore cannot inject a command.

Emits ``KEY=VALUE``: ``PROFILE_PATH``, ``PROFILE_NAME``, ``BRANCH_DESCRIBE``,
``TICKET_URL_TEMPLATE``, ``HAS_TICKET_FETCH``, ``HAS_PR_OPEN``, ``CAP_AIC``.

Exit codes:
    2   Usage error.
    3   No profile found.
    4   Profile is invalid.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.cli import emit_all, run_main  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402

#: This skill's install root; ``../`` from this file.
SKILL_DIR = Path(__file__).resolve().parents[1]

#: Bundled profiles, and the home of the ``default`` fallback.
BUNDLED_DIR = SKILL_DIR / "templates" / "profiles"

#: Repo-local profile filename, checked first.
REPO_PROFILE_NAME = ".dev.toml"

#: Per-user profile, checked when the repo does not carry one.
USER_PROFILE = Path.home() / ".config" / "dev" / "profile.toml"

#: Vendor-neutral PR skeleton used when no repo template matches.
PR_FALLBACK = SKILL_DIR / "templates" / "pr-fallback.md"

#: Groups the branch pattern must capture for branch-parse.py to work.
REQUIRED_GROUPS = ("ticket", "slug")

#: Accepted ``[branch].ticket_case`` values.
_CASE_MODES = ("upper", "lower", "preserve")

#: Fallback when ``[budget].cap_aic`` is absent.
DEFAULT_CAP_AIC = 7000


@dataclass(frozen=True)
class Profile:
    """A validated platform profile.

    Attributes:
        path: File the profile was loaded from.
        name: ``name`` key, defaulting to the file stem.
        pattern: Compiled ``[branch].pattern``.
        describe: Human-readable branch convention, quoted on rejection.
        ticket_case: One of ``upper``, ``lower``, ``preserve``.
        ticket_url: ``[ticket].url`` template containing ``{ticket}``.
        ticket_fetch: Argv list that prints the ticket, or empty if unset.
        pr_templates: Worktree-relative PR template candidates, in order.
        pr_open: Argv list that opens the PR, or empty if unset.
        cap_aic: Monthly credit allowance.
    """

    path: Path
    name: str
    pattern: re.Pattern[str]
    describe: str
    ticket_case: str
    ticket_url: str
    ticket_fetch: list[str] = field(default_factory=list)
    pr_templates: list[str] = field(default_factory=list)
    pr_open: list[str] = field(default_factory=list)
    cap_aic: int = DEFAULT_CAP_AIC


def _invalid(path: Path, problem: str) -> SkillError:
    """Build the uniform 'invalid profile' error (exit 4)."""
    return SkillError(f"invalid profile '{path}': {problem}", code=4)


def _table(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    """Return the ``[key]`` table, erroring if present but not a table."""
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise _invalid(path, f"[{key}] must be a table")
    return value


def _string_list(value: Any, path: Path, label: str) -> list[str]:
    """Coerce *value* to a list of strings, erroring on any other shape.

    Argv lists and template lists are both validated here so a profile that
    writes a command as a single shell string fails loudly at load time rather
    than silently becoming an unrunnable one-element argv.
    """
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid(path, f"{label} must be a list of strings")
    return list(value)


def _compile_pattern(raw: Any, path: Path) -> re.Pattern[str]:
    """Compile ``[branch].pattern`` and assert it captures the needed groups."""
    if not isinstance(raw, str) or not raw:
        raise _invalid(path, "[branch].pattern is required and must be a string")
    try:
        pattern = re.compile(raw)
    except re.error as exc:
        raise _invalid(path, f"[branch].pattern does not compile: {exc}") from exc
    missing = [g for g in REQUIRED_GROUPS if g not in pattern.groupindex]
    if missing:
        raise _invalid(
            path,
            "[branch].pattern must capture named group(s): " + ", ".join(missing),
        )
    return pattern


def _parse(path: Path) -> Profile:
    """Load and validate the profile at *path*.

    Raises:
        SkillError: Code 4 if the file is unreadable, is not valid TOML, or
            violates the schema.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _invalid(path, f"cannot read file: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise _invalid(path, f"not valid TOML: {exc}") from exc

    branch = _table(data, "branch", path)
    ticket = _table(data, "ticket", path)
    pull_request = _table(data, "pr", path)
    budget = _table(data, "budget", path)

    pattern = _compile_pattern(branch.get("pattern"), path)

    ticket_case = branch.get("ticket_case", "preserve")
    if ticket_case not in _CASE_MODES:
        raise _invalid(
            path,
            f"[branch].ticket_case must be one of {', '.join(_CASE_MODES)}",
        )

    ticket_url = ticket.get("url", "")
    if not isinstance(ticket_url, str) or "{ticket}" not in ticket_url:
        raise _invalid(path, "[ticket].url is required and must contain '{ticket}'")

    cap = budget.get("cap_aic", DEFAULT_CAP_AIC)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        raise _invalid(path, "[budget].cap_aic must be a positive integer")

    describe = branch.get("describe", "")
    if not isinstance(describe, str):
        raise _invalid(path, "[branch].describe must be a string")

    name = data.get("name", path.stem)
    if not isinstance(name, str):
        raise _invalid(path, "name must be a string")

    return Profile(
        path=path,
        name=name,
        pattern=pattern,
        describe=describe or pattern.pattern,
        ticket_case=ticket_case,
        ticket_url=ticket_url,
        ticket_fetch=_string_list(ticket.get("fetch"), path, "[ticket].fetch"),
        pr_templates=_string_list(pull_request.get("templates"), path, "[pr].templates"),
        pr_open=_string_list(pull_request.get("open"), path, "[pr].open"),
        cap_aic=cap,
    )


def _resolve_override(override: str) -> Path:
    """Map ``--profile`` to a file: a bundled name, else an explicit path."""
    bundled = BUNDLED_DIR / f"{override}.toml"
    if bundled.is_file():
        return bundled
    candidate = Path(override).expanduser()
    if candidate.is_file():
        return candidate
    available = sorted(p.stem for p in BUNDLED_DIR.glob("*.toml"))
    raise SkillError(
        f"profile '{override}' is neither a bundled profile nor a readable file "
        f"(bundled: {', '.join(available) or 'none'}).",
        code=3,
    )


def find_profile(repo: str | Path | None = None, override: str = "") -> Path:
    """Return the profile file to use.

    Args:
        repo: Repo/worktree root searched for ``.dev.toml``.
        override: ``--profile`` value; a bundled name or a path.

    Raises:
        SkillError: Code 3 when nothing is found (only possible if the bundled
            default is missing, i.e. a broken install).
    """
    if override:
        return _resolve_override(override)
    if repo:
        repo_profile = Path(repo).expanduser() / REPO_PROFILE_NAME
        if repo_profile.is_file():
            return repo_profile
    if USER_PROFILE.is_file():
        return USER_PROFILE
    default = BUNDLED_DIR / "default.toml"
    if default.is_file():
        return default
    raise SkillError(
        f"no profile found and the bundled default is missing at {default}.",
        code=3,
    )


def load_profile(repo: str | Path | None = None, override: str = "") -> Profile:
    """Find and validate the applicable profile. The helper other scripts use."""
    return _parse(find_profile(repo, override))


def render_command(
    argv: Sequence[str], values: dict[str, str]
) -> list[str]:
    """Substitute ``{placeholder}`` tokens per element and expand leading ``~``.

    Substitution is per element and the result is spawned without a shell, so a
    value containing spaces, quotes or shell metacharacters stays a single
    argument and cannot inject a command. An unknown placeholder is left
    verbatim rather than raising, so a profile using a token this call does not
    supply fails visibly in the command itself.
    """
    rendered: list[str] = []
    for element in argv:
        out = element
        for key, value in values.items():
            out = out.replace("{" + key + "}", value)
        rendered.append(str(Path(out).expanduser()) if out.startswith("~") else out)
    return rendered


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="profile_resolve.py",
        description="Resolve and validate the dev platform profile.",
    )
    parser.add_argument("--repo", default="", help="Repo root searched for .dev.toml.")
    parser.add_argument("--profile", default="", help="Bundled profile name or path.")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Accepted for symmetry; every resolve already validates.",
    )
    known, extra = parser.parse_known_args(argv)
    if extra:
        raise UsageError(f"unknown arg '{extra[0]}'")

    profile = load_profile(known.repo or None, known.profile)

    emit_all({
        "PROFILE_PATH": str(profile.path),
        "PROFILE_NAME": profile.name,
        "BRANCH_DESCRIBE": profile.describe,
        "TICKET_URL_TEMPLATE": profile.ticket_url,
        "HAS_TICKET_FETCH": "1" if profile.ticket_fetch else "0",
        "HAS_PR_OPEN": "1" if profile.pr_open else "0",
        "CAP_AIC": str(profile.cap_aic),
    })
    return 0


if __name__ == "__main__":
    run_main(main)
