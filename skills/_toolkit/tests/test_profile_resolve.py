#!/usr/bin/env python3
"""Tests for profile_resolve.py — lookup order, validation, command safety."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

from profile_resolve import (  # noqa: E402
    BUNDLED_DIR,
    find_profile,
    load_profile,
    render_command,
)

MINIMAL = """
name = "unit"
[branch]
pattern = '^(?P<ticket>[A-Z]+-[0-9]+)-(?P<slug>[a-z-]+)$'
[ticket]
url = "https://t/{ticket}"
"""


def write(directory: Path, name: str, text: str) -> Path:
    """Write *text* to ``directory/name`` and return the path."""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the entry point as a subprocess to assert its exit codes."""
    return subprocess.run(
        [sys.executable, str(LIB / "profile_resolve.py"), *args],
        capture_output=True, text=True,
    )


class TestLookupOrder(unittest.TestCase):
    """The repo profile wins over the bundled default."""

    def test_repo_profile_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo, ".dev.toml", MINIMAL)
            self.assertEqual(find_profile(repo), repo / ".dev.toml")

    def test_falls_back_to_bundled_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # No repo profile; the user profile is absent on a clean machine,
            # so this must land on the bundled default.
            found = find_profile(Path(tmp))
            self.assertEqual(found.name, "default.toml")
            self.assertEqual(found.parent, BUNDLED_DIR)

    def test_override_by_bundled_name(self) -> None:
        self.assertEqual(
            find_profile(None, "example-github").stem, "example-github"
        )

    def test_override_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), "custom.toml", MINIMAL)
            self.assertEqual(find_profile(None, str(path)), path)

    def test_override_beats_repo_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write(repo, ".dev.toml", MINIMAL)
            self.assertEqual(
                find_profile(repo, "example-github").stem, "example-github"
            )

    def test_unknown_override_exits_3(self) -> None:
        self.assertEqual(run_cli("--profile", "no-such-profile").returncode, 3)


class TestValidation(unittest.TestCase):
    """Schema violations are rejected at load time with exit 4."""

    def _expect_invalid(self, text: str, needle: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), "p.toml", text)
            result = run_cli("--profile", str(path))
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn(needle, result.stderr)

    def test_uncompilable_pattern(self) -> None:
        self._expect_invalid(
            '[branch]\npattern = "(?P<ticket>["\n[ticket]\nurl = "{ticket}"\n',
            "does not compile",
        )

    def test_missing_ticket_group(self) -> None:
        self._expect_invalid(
            "[branch]\npattern = '^(?P<slug>[a-z]+)$'\n[ticket]\nurl = \"{ticket}\"\n",
            "ticket",
        )

    def test_missing_slug_group(self) -> None:
        self._expect_invalid(
            "[branch]\npattern = '^(?P<ticket>[A-Z]+)$'\n[ticket]\nurl = \"{ticket}\"\n",
            "slug",
        )

    def test_url_without_placeholder(self) -> None:
        self._expect_invalid(
            "[branch]\npattern = '^(?P<ticket>[A-Z]+)-(?P<slug>[a-z]+)$'\n"
            '[ticket]\nurl = "https://t/browse"\n',
            "{ticket}",
        )

    def test_command_as_string_is_rejected(self) -> None:
        """A shell-string command must fail loudly, not become a bad argv."""
        self._expect_invalid(
            MINIMAL + '\nfetch = "gh issue view {ticket}"\n',
            "list of strings",
        )

    def test_bad_ticket_case(self) -> None:
        self._expect_invalid(
            "[branch]\npattern = '^(?P<ticket>[A-Z]+)-(?P<slug>[a-z]+)$'\n"
            'ticket_case = "title"\n[ticket]\nurl = "{ticket}"\n',
            "ticket_case",
        )

    def test_bad_cap(self) -> None:
        self._expect_invalid(MINIMAL + "\n[budget]\ncap_aic = 0\n", "cap_aic")

    def test_not_toml(self) -> None:
        self._expect_invalid("this is not : toml =\n", "not valid TOML")


class TestBundledProfiles(unittest.TestCase):
    """Every shipped profile must load, or the skill is broken on install."""

    def test_all_bundled_profiles_are_valid(self) -> None:
        found = sorted(BUNDLED_DIR.glob("*.toml"))
        self.assertGreaterEqual(len(found), 3)
        for path in found:
            with self.subTest(profile=path.stem):
                self.assertTrue(load_profile(None, str(path)).ticket_url)

    def test_default_configures_no_vendor_commands(self) -> None:
        """The default must work anywhere, so it may not assume a tool."""
        profile = load_profile(None, "default")
        self.assertEqual(profile.ticket_fetch, [])
        self.assertEqual(profile.pr_open, [])

    def test_example_commands_point_at_real_entry_points(self) -> None:
        """Examples are copied verbatim, so a dead path is a real defect.

        Skipped per-command when the companion skill is not installed — the
        examples are illustrative and must not fail on a machine without them.
        """
        skills_root = Path.home() / ".agents" / "skills"
        checked = 0
        for path in sorted(BUNDLED_DIR.glob("example-*.toml")):
            profile = load_profile(None, str(path))
            for command in (profile.ticket_fetch, profile.pr_open):
                if not command:
                    continue
                entry = Path(command[0]).expanduser()
                if skills_root not in entry.parents:
                    continue  # a bare tool such as `gh`, resolved from PATH
                if not entry.parent.parent.is_dir():
                    continue  # companion skill not installed here
                with self.subTest(profile=path.stem, entry=entry.name):
                    self.assertTrue(entry.is_file(), f"{entry} does not exist")
                    checked += 1
        if checked == 0:
            self.skipTest("no companion skills are installed here")


class TestRenderCommand(unittest.TestCase):
    """Placeholder substitution is per element and never shell-interpreted."""

    def test_substitutes_per_element(self) -> None:
        self.assertEqual(
            render_command(["gh", "issue", "view", "{ticket}"], {"ticket": "42"}),
            ["gh", "issue", "view", "42"],
        )

    def test_injection_stays_one_argument(self) -> None:
        rendered = render_command(["echo", "{title}"], {"title": "a; rm -rf /"})
        self.assertEqual(rendered, ["echo", "a; rm -rf /"])
        self.assertEqual(len(rendered), 2)

    def test_expands_tilde(self) -> None:
        rendered = render_command(["~/bin/x", "{ticket}"], {"ticket": "1"})
        self.assertTrue(rendered[0].startswith(str(Path.home())))

    def test_unknown_placeholder_left_verbatim(self) -> None:
        self.assertEqual(render_command(["{nope}"], {"ticket": "1"}), ["{nope}"])


class TestCli(unittest.TestCase):
    """The emitted metadata block is the caller-facing API."""

    def test_emits_expected_keys(self) -> None:
        result = run_cli("--profile", "example-github")
        self.assertEqual(result.returncode, 0, result.stderr)
        for key in (
            "PROFILE_PATH", "PROFILE_NAME", "BRANCH_DESCRIBE",
            "TICKET_URL_TEMPLATE", "HAS_TICKET_FETCH", "HAS_PR_OPEN", "CAP_AIC",
        ):
            self.assertIn(f"{key}=", result.stdout)

    def test_unknown_arg_is_usage_error(self) -> None:
        self.assertEqual(run_cli("--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
