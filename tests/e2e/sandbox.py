"""Build a throwaway fixture repository with a bare origin for one e2e run."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HUMAN = ("E2E Human", "e2e-human@example.invalid")


@dataclass(frozen=True)
class Sandbox:
    repo: Path
    origin: Path
    base: str

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", "-c", "safe.bareRepository=all", *args], cwd=cwd or self.repo,
                              check=True, capture_output=True, text=True).stdout.strip()

    def origin_head(self) -> str:
        return self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main")

    def pushed(self) -> list[str]:
        """Commits pushed to origin since the base, newest first."""
        log = self.git("--git-dir", str(self.origin), "rev-list", f"{self.base}..refs/heads/main")
        return log.split()


def create(fixture: str, root: Path) -> Sandbox:
    """Copy ``fixtures/<fixture>`` into ``root/repo``, commit it and push to ``root/origin.git``."""
    repo, origin = root / "repo", root / "origin.git"
    shutil.copytree(FIXTURES / fixture, repo)
    run = lambda *a, cwd=repo: subprocess.run(a, cwd=cwd, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=root)
    run("git", "init", "-q", "-b", "main")
    # A fixed identity keeps Approved-by deterministic and away from the real human.
    run("git", "config", "user.name", HUMAN[0])
    run("git", "config", "user.email", HUMAN[1])
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", f"Initial {fixture} fixture")
    run("git", "remote", "add", "origin", str(origin))
    run("git", "push", "-q", "-u", "origin", "main")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()
    return Sandbox(repo, origin, base)
