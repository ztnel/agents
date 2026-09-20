#!/usr/bin/env python3
"""Render a worktree's ``PR.md`` from the profile's pull-request template.

Template resolution is profile-driven: each ``[pr].templates`` entry is tried
relative to the worktree, then this skill's vendor-neutral
``templates/pr-fallback.md``. Rendering is done here rather than delegated, so
no vendor skill is a hard dependency — a repo with no template still gets a
usable PR body.

Placement of the ticket link is deliberately convention-based rather than
title-based, so it works for any tracker:

1. ``{TICKET_URL}`` anywhere in the template is substituted directly.
2. Otherwise the URL is inserted under the template's **first** top-level
   heading — the section every PR template opens with for exactly this purpose
   — and an immediately-following ``_italic placeholder_`` line is dropped.
3. A template with no heading gets the URL prepended.

``--description`` fills a ``{DESCRIPTION}`` placeholder, else the first heading
whose text contains "description", else it is appended.

``PR.md`` is added to the worktree's ``.git/info/exclude`` (unless
``--no-exclude``) so a review diff never shows it. The path comes from
``git rev-parse --git-path info/exclude`` because in a linked worktree
``$GIT_DIR`` is the per-worktree gitdir while ``info/exclude`` resolves to the
COMMON dir; writing to the wrong file is silently ignored.

Emits ``PR_FILE`` and ``TEMPLATE_SOURCE``.

Exit codes:
    2   Usage error.
    3   Worktree missing.
    4   ``PR.md`` exists (use ``--force``).
    5   No template found and the bundled fallback is missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from profile_resolve import PR_FALLBACK, Profile, load_profile  # noqa: E402
from skillkit.cli import emit_all, run_main  # noqa: E402
from skillkit.errors import SkillError, UsageError  # noqa: E402
from skillkit.proc import run  # noqa: E402

#: Line appended to .git/info/exclude so the rendered body stays out of diffs.
EXCLUDE_LINE = "PR.md"


def resolve_template(worktree: Path, profile: Profile) -> Path:
    """First existing ``[pr].templates`` entry, else the bundled fallback.

    Raises:
        SkillError: Code 5 if nothing matches and the fallback is missing.
    """
    for candidate in profile.pr_templates:
        path = worktree / candidate
        if path.is_file():
            return path
    if PR_FALLBACK.is_file():
        return PR_FALLBACK
    raise SkillError(
        f"no PR template matched and the bundled fallback is missing at "
        f"{PR_FALLBACK}.",
        code=5,
    )


def _is_placeholder(line: str) -> bool:
    """True for a ``_prompt text_`` line templates use as section guidance."""
    stripped = line.strip()
    return len(stripped) > 1 and stripped.startswith("_") and stripped.endswith("_")


def _insert_after_heading(
    lines: list[str], block: list[str], match_text: str = ""
) -> list[str]:
    """Insert *block* after a heading, dropping its italic placeholder.

    Args:
        lines: Template lines.
        block: Lines to insert.
        match_text: Lowercase substring the heading must contain. Empty means
            the first top-level heading, whatever it says.

    Returns:
        list[str]: Rewritten lines, or unchanged when no heading matches.
    """
    output: list[str] = []
    inserted = False
    index = 0
    while index < len(lines):
        line = lines[index]
        output.append(line)
        heading = line.lstrip().startswith("#")
        if not inserted and heading and match_text in line.lower():
            output.extend(block)
            if index + 1 < len(lines) and _is_placeholder(lines[index + 1]):
                index += 1
            inserted = True
        index += 1
    return output if inserted else lines


def render_body(template_text: str, ticket_url: str, description: str) -> str:
    """Fill *template_text* with the ticket URL and optional description."""
    if "{TICKET_URL}" in template_text:
        template_text = template_text.replace("{TICKET_URL}", ticket_url)
        lines = template_text.splitlines()
    else:
        lines = _insert_after_heading(template_text.splitlines(), ["", ticket_url])
        if lines == template_text.splitlines():
            lines = [ticket_url, ""] + lines

    if description:
        body = description.rstrip("\n").splitlines()
        joined = "\n".join(lines)
        if "{DESCRIPTION}" in joined:
            lines = joined.replace("{DESCRIPTION}", description.rstrip("\n")).splitlines()
        else:
            filled = _insert_after_heading(lines, ["", *body], "description")
            lines = filled if filled != lines else [*lines, "", *body]

    return "\n".join(lines) + "\n"


def _resolved_exclude(worktree: Path) -> Path | None:
    """Path git actually reads for ``info/exclude`` in *worktree*.

    In a linked worktree ``info/exclude`` lives in the COMMON gitdir, so only
    the path git itself reports is safe to write.
    """
    result = run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=str(worktree))
    if not result.ok or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else (worktree / path)


def _append_exclude_line(excl: Path, line: str) -> None:
    """Append *line* to *excl* unless already present as a full line."""
    excl.parent.mkdir(parents=True, exist_ok=True)
    existing = excl.read_text(encoding="utf-8", errors="replace") if excl.exists() else ""
    if any(row.rstrip() == line for row in existing.splitlines()):
        return
    with open(excl, "a", encoding="utf-8") as stream:
        if existing and not existing.endswith("\n"):
            stream.write("\n")
        stream.write(f"{line}\n")


def main(argv: list[str]) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        prog="pr_render.py",
        description="Render PR.md from the profile's pull-request template.",
    )
    parser.add_argument("--worktree", default="", help="Worktree receiving PR.md.")
    parser.add_argument("--ticket-url", default="", help="Full ticket URL.")
    parser.add_argument("--title", default="", help="Optional one-line PR title.")
    parser.add_argument("--description", default="", help="File filling the Description.")
    parser.add_argument("--profile", default="", help="Bundled profile name or path.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing PR.md.")
    parser.add_argument(
        "--no-exclude",
        dest="exclude",
        action="store_false",
        help="Do NOT add PR.md to .git/info/exclude.",
    )
    known, extra = parser.parse_known_args(argv)
    if extra:
        raise UsageError(f"unknown arg '{extra[0]}'")
    if not known.worktree:
        raise UsageError("--worktree is required")
    if not known.ticket_url:
        raise UsageError("--ticket-url is required")

    worktree = Path(known.worktree).expanduser()
    if not worktree.is_dir():
        raise SkillError(f"worktree '{known.worktree}' does not exist", code=3)

    out = worktree / "PR.md"
    if out.exists() and not known.force:
        raise SkillError(f"{out} already exists (use --force to overwrite).", code=4)

    profile = load_profile(worktree, known.profile)
    template = resolve_template(worktree, profile)

    description = ""
    if known.description:
        description_path = Path(known.description).expanduser()
        if not description_path.is_file():
            raise SkillError(
                f"description file '{known.description}' not found", code=3
            )
        description = description_path.read_text(encoding="utf-8")

    body = render_body(
        template.read_text(encoding="utf-8"), known.ticket_url, description
    )

    with open(out, "w", encoding="utf-8") as stream:
        if known.title:
            stream.write(f"<!-- PR title: {known.title} -->\n\n")
        stream.write(body)

    if known.exclude:
        excl = _resolved_exclude(worktree)
        if excl is not None:
            _append_exclude_line(excl, EXCLUDE_LINE)

    emit_all({"PR_FILE": str(out), "TEMPLATE_SOURCE": str(template)})
    return 0


if __name__ == "__main__":
    run_main(main)
