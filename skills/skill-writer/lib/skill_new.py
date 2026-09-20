#!/usr/bin/env python3
"""Scaffold a compliant Agent Skill and lint it without altering existing work."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit.cli import run_main
from skillkit.errors import SkillError, UsageError
from skillkit.proc import run, which

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def usage() -> None:
    """Print the public skill scaffold contract.

    Returns:
        None.
    """
    default_root = Path(__file__).resolve().parents[2]
    print(f"""Usage: skill_new.py <skill-name> [--root <dir>]

Creates <root>/<skill-name>/ containing a spec-compliant SKILL.md, a README.md
stub and an empty lib/, then validates it.

  <skill-name>   lowercase a-z, 0-9 and single hyphens; no leading, trailing or
                 consecutive hyphens; max 64 characters
  --root <dir>   where to create the skill (default: {default_root})""")


def take_value(argv: list[str], option: str) -> str:
    """Consume one required option value.

    Args:
        argv: Mutable remaining argument list.
        option: Flag requiring a value.

    Returns:
        Following argument value.

    Raises:
        UsageError: If absent.
    """
    if not argv:
        raise UsageError(f"{option} needs a directory")
    return argv.pop(0)


def parse_arguments(argv: Sequence[str]) -> tuple[str, Path]:
    """Parse and validate skill name and destination root.

    Args:
        argv: Command-line arguments excluding program name.

    Returns:
        ``(skill_name, root_directory)``.

    Raises:
        UsageError: If the name/options are invalid.
    """
    name = ""
    root = Path(__file__).resolve().parents[2]
    remaining = list(argv)
    while remaining:
        arg = remaining.pop(0)
        if arg in {"-h", "--help"}:
            usage()
            raise SystemExit(0)
        if arg == "--root":
            root = Path(take_value(remaining, arg))
        elif name:
            raise UsageError(f"unexpected argument: {arg}")
        else:
            name = arg
    if not name:
        usage()
        raise SystemExit(2)
    if len(name) > 64:
        raise UsageError(f"name is {len(name)} characters (max 64)")
    if not NAME_RE.fullmatch(name):
        raise UsageError(
            f"""invalid skill name: '{name}'
       Must be lowercase a-z, 0-9 and single hyphens, with no leading, trailing
       or consecutive hyphens. The runtime skips skills whose name breaks these
       rules, without logging anything."""
        )
    return name, root


def readme(name: str) -> str:
    """Build the initial README stub for a new skill.

    Args:
        name: Valid new skill name.

    Returns:
        README Markdown content.
    """
    return f"""# `{name}`

ONE_LINE_PURPOSE

## Public API

### `lib/<entry>.py`

```bash
~/.agents/skills/{name}/lib/<entry>.py <args>
```

Describe each entry point's arguments, exit codes and side effects.

## Design

Why this skill is structured the way it is, and what it deliberately does not do.

## Dependencies

`python3`, and the shared `~/.agents/skills/_lib/skillkit` package.
"""


def lint(destination: Path) -> None:
    """Run the existing linter as advisory output for a fresh skeleton.

    Args:
        destination: Newly created skill directory.

    Returns:
        None.

    Side Effects:
        Executes the read-only local linter and relays its output. Its expected
        placeholder warning/failure never removes the scaffold.
    """
    linter = Path(__file__).with_name("skill-lint.py")
    if not linter.is_file() or which("python3") is None:
        return
    result = run([sys.executable, str(linter), str(destination)])
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)


def main(argv: Sequence[str]) -> int:
    """Create a new skill skeleton without overwriting an existing directory.

    Args:
        argv: Command-line arguments excluding program name.

    Returns:
        Process exit status.

    Raises:
        SkillError: If the template or destination makes scaffolding unsafe.
        UsageError: If the invocation is malformed.

    Side Effects:
        Creates only the requested new skill directory and its initial files.
    """
    try:
        name, root = parse_arguments(argv)
    except SystemExit as exc:
        return int(exc.code)
    template = Path(__file__).resolve().parents[1] / "templates" / "SKILL.md.template"
    if not template.is_file():
        raise SkillError(f"template not found: {template}")
    destination = root / name
    if destination.exists():
        raise SkillError(f"already exists: {destination}")
    (destination / "lib").mkdir(parents=True)
    (destination / "SKILL.md").write_text(
        template.read_text(encoding="utf-8").replace("SKILL_NAME", name),
        encoding="utf-8",
    )
    (destination / "README.md").write_text(readme(name), encoding="utf-8")
    print(f"Created {destination}")
    print()
    lint(destination)
    print(f"""
Next:
  1. Replace the placeholder description. It must be under 1024 characters and
     say when to use the skill, not just what it does.
  2. Write the body, keeping it under 500 lines.
  3. Write lib/<entry>.py in Python — shell scripts are not accepted. Give it a
     '#!/usr/bin/env python3' shebang, chmod +x, and reuse
     ~/.agents/skills/_lib/skillkit rather than re-solving portability.
  4. Gate on: python3 {Path(__file__).with_name('skill-lint.py')} {destination} --strict""")
    return 0


if __name__ == "__main__":
    run_main(main)
