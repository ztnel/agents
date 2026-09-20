"""Error types carrying the process exit code a skill should terminate with.

The shell scripts these replace signalled failure with distinct exit codes
(``exit 3`` missing dependency, ``exit 4`` bad tmux/session target, ...) and
callers — including the test suites — assert on them. Raising an exception that
carries its own exit code keeps those contracts intact while still unwinding
cleanly through Python.
"""

from __future__ import annotations


class SkillError(Exception):
    """A fatal, already-diagnosed condition.

    Args:
        message: Human-facing reason, printed to stderr without a traceback.
        code: Process exit status. Defaults to 1.
        hint: Optional second line suggesting the remedy.
        raw: The message is already fully formatted and must be printed
            verbatim, without the ``ERROR: `` prefix. For scripts whose error
            format predates this package and is grepped for by callers.
    """

    def __init__(
        self,
        message: str,
        code: int = 1,
        hint: str | None = None,
        raw: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.raw = raw


class UsageError(SkillError):
    """Bad invocation. Exits 2, matching the shell scripts' convention."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message, code=2, hint=hint)


class MissingDependency(SkillError):
    """A required external command is not on PATH. Exits 3."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message, code=3, hint=hint)
