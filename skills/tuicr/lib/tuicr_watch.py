#!/usr/bin/env python3
"""tuicr-watch — wake a Copilot CLI session when a review comment needs a reply.

Watches one tuicr review session and, whenever a comment is awaiting a
response, delivers a **terse wake** into the agent's CLI pane. The prompt names
only the session and the pending count; what the woken agent must *do* is
defined by the tuicr skill's "Wake contract", so behaviour is identical whether
the woken session is a lone coding agent or an orchestrator brokering to
sub-agents.

Transport: paste through a tmux buffer, submit with the CLI's CSI-u Enter
sequence (named ``Enter`` as fallback), then confirm the wake was **accepted**
by finding it in the session's persisted user-message events. Terminal
rendering is only ever a diagnostic, never the success oracle.

SAFETY (local-only): this tool reads comments and delivers a wake. The woken
agent is bound by the skill's contract to keep edits UNSTAGED and post local
draft replies only — never commit, push, or sync to a remote forge.

Ported from tuicr_watch.py. The shell version spawned ``python3`` at 11 sites,
two of them *per iteration* of the submit-confirmation loop, costing ~65 ms per
100 ms poll step. Here every one of those is an in-process call.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

from skillkit import copilot, paths, tmuxio, tuicrio  # noqa: E402
from skillkit.cli import detail, info, run_main, warn  # noqa: E402
from skillkit.errors import SkillError  # noqa: E402
from skillkit.lock import PidFile  # noqa: E402
from skillkit.proc import require  # noqa: E402
from skillkit.tuicrio import Comment, TuicrError  # noqa: E402

#: Input-box states reported by :meth:`WakeDeliverer.box_state`.
BOX_HAS_WAKE = 0
BOX_EMPTY = 1
BOX_OTHER_TEXT = 2
BOX_UNRECOGNIZED = 3

#: A horizontal rule bounding the CLI input box must be at least this long and
#: this proportion box-drawing characters to count as a rule rather than prose.
_RULE_MIN_WIDTH = 20
_RULE_MIN_RATIO = 0.8

#: Consecutive read failures before checking whether the session still exists,
#: and the warning cadence. At the default 1.5 s interval this is a ~60 s grace
#: period, long enough that restarting tuicr never looks like the review ending.
_VANISH_CHECK_EVERY = 40


@dataclass
class WatchConfig:
    """Resolved options for one watcher run."""

    repo: str = "."
    session: str = ""
    cli_session: str = ""
    cli_pane: str = ""
    interval: float = 1.5
    submit_tries: int = 3
    submit_step: float = 0.1
    event_timeout: float = 3.0
    queue_timeout: float = 300.0
    rearm: float = 900.0
    max_attempts: int = 3
    ignore_types: list[str] = field(default_factory=lambda: list(tuicrio.DEFAULT_IGNORE_TYPES))
    replay: bool = False
    seed_existing: bool = False
    once: bool = False
    dry_run: bool = False
    state_dir: Path = field(default_factory=lambda: paths.state_dir("tuicr", "watch"))
    session_state_dir: Path | None = None


class Ledger:
    """Per-comment delivery history, keyed by comment id.

    Persisted as JSON so a restarted daemon does not replay an old backlog, and
    so a comment that never received a reply can be re-armed after a timeout
    without being delivered forever.
    """

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._entries: dict[str, dict] = paths.read_json(path, default={}) or {}

    def is_empty(self) -> bool:
        """Whether nothing has ever been recorded."""
        return not self._entries

    def suppresses(self, comment_id: str, *, rearm: float, max_attempts: int, now: float) -> bool:
        """Whether *comment_id* should be withheld from this delivery round.

        Suppressed when its attempt budget is spent, when re-arming is disabled,
        or when the re-arm interval has not yet elapsed.
        """
        entry = self._entries.get(comment_id)
        if not entry:
            return False
        attempts = int(entry.get("attempts", 0))
        last = float(entry.get("last", 0))
        return attempts >= max_attempts or rearm <= 0 or (now - last) < rearm

    def record(self, comment_ids: list[str], live_ids: list[str] | None = None) -> None:
        """Count a delivery attempt against each id, then prune dead ids.

        Args:
            comment_ids: Ids just delivered.
            live_ids: Every id still present in the session. Ids outside this
                set are dropped so the ledger stays bounded over a long review.
        """
        if not self.enabled:
            return
        now = time.time()
        for comment_id in comment_ids:
            entry = self._entries.get(comment_id) or {}
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            entry["last"] = now
            self._entries[comment_id] = entry
        if live_ids:
            keep = set(live_ids)
            self._entries = {k: v for k, v in self._entries.items() if k in keep}
        paths.write_json_atomic(self.path, self._entries)


def select_unanswered(comments: list[Comment], config: WatchConfig) -> list[Comment]:
    """Every comment still awaiting a response, regardless of delivery history.

    A comment is unanswered when it carries an id, is not an ignored type, and
    has no reply posted after it **at its exact anchor**. This is the honest
    measure of outstanding review work, so it is what the wake prompt counts —
    the agent is told how much is waiting, not merely how much happened to
    change since the last poll.
    """
    ignore = set(config.ignore_types)
    replies = tuicrio.replies_by_anchor(comments)
    return [
        comment
        for comment in comments
        if comment.id and comment.comment_type not in ignore and not tuicrio.is_answered(comment, replies)
    ]


def select_pending(
    comments: list[Comment],
    ledger: Ledger,
    config: WatchConfig,
) -> list[Comment]:
    """Unanswered comments that are due for delivery on this round.

    The ledger holds back ids already delivered recently, so a burst of polls
    does not re-wake the agent for the same comment; ``--replay`` ignores that
    history entirely.
    """
    unanswered = select_unanswered(comments, config)
    if config.replay:
        return unanswered
    now = time.time()
    return [
        comment
        for comment in unanswered
        if not ledger.suppresses(
            comment.id, rearm=config.rearm, max_attempts=config.max_attempts, now=now
        )
    ]


def build_prompt(session: str, repo_key: str, count: int, wake_token: str) -> str:
    """The wake prompt.

    Deliberately minimal: it names the session and the pending count and points
    at the contract. Keeping the human's comment text out of it means the agent
    is never fed its own review surface as input, and keeps the prompt terse
    regardless of how much was written.

    Args:
        count: **Total** comments awaiting a response, not just the ones that
            triggered this wake. A count of only the trigger would understate
            the work whenever earlier comments are still outstanding.
    """
    noun = "comment" if count == 1 else "comments"
    return (
        f"tuicr wake {wake_token}: {count} pending {noun} in review session "
        f"{session} ({repo_key}). Follow the tuicr skill Wake contract."
    )


class WakeDeliverer:
    """Delivers a wake into a bound pane and proves it was accepted."""

    def __init__(self, config: WatchConfig, session: copilot.CliSession, pane: str, repo_key: str) -> None:
        self.config = config
        self.session = session
        self.pane = pane
        self.repo_key = repo_key
        self._focus_injected = False
        self._event_offset = 0

    def box_state(self, needle: str) -> int:
        """Classify the CLI input box's contents.

        The box is the region between the last two horizontal rules in the
        pane capture. Returns one of :data:`BOX_HAS_WAKE`, :data:`BOX_EMPTY`,
        :data:`BOX_OTHER_TEXT` or :data:`BOX_UNRECOGNIZED`.

        The state is advisory only: wake delivery still proceeds even when the
        box already holds text or the capture shape is unfamiliar.
        """
        captured = tmuxio.capture(self.pane)
        if captured is None:
            return BOX_UNRECOGNIZED
        lines = captured.splitlines()

        def is_rule(line: str) -> bool:
            stripped = line.strip()
            return len(stripped) >= _RULE_MIN_WIDTH and stripped.count("\u2500") >= len(stripped) * _RULE_MIN_RATIO

        rules = [index for index, line in enumerate(lines) if is_rule(line)]
        if len(rules) < 2:
            return BOX_UNRECOGNIZED

        box_lines = lines[rules[-2] + 1 : rules[-1]]
        if box_lines:
            box_lines[0] = _strip_prompt_marker(box_lines[0])
        box = "".join("".join(line.split()) for line in box_lines)
        wanted = "".join(needle.split())
        if wanted and wanted in box:
            return BOX_HAS_WAKE
        return BOX_EMPTY if not box else BOX_OTHER_TEXT

    def wait_for_event(self, wake_token: str, timeout: float | None = None) -> bool:
        """Poll the session's event log until *wake_token* is persisted.

        The shell version spawned two ``python3`` processes per iteration here —
        one to scan, one to compare the deadline — which inflated a 0.1 s step
        to ~0.165 s. Both are now in-process, so the step is the step.
        """
        limit = self.config.event_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            if copilot.events_contain(self.session.events_file, wake_token, from_offset=self._event_offset):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.config.submit_step)

    def _restore_focus(self) -> None:
        """Undo an injected focus-in, leaving the human's focus as it was."""
        if not self._focus_injected:
            return
        if not tmuxio.window_is_active(self.pane):
            tmuxio.send_focus_out(self.pane)
        self._focus_injected = False

    def _submit(self, mode: str) -> None:
        """Press Enter in the CLI input box.

        With ``focus-events`` on, the CLI ignores synthetic submits once its
        window has reported focus-out. Restoring logical focus for the target
        pane makes the submit land without switching the human's active window.
        """
        if not tmuxio.window_is_active(self.pane):
            tmuxio.send_focus_in(self.pane)
            self._focus_injected = True
        tmuxio.submit_enter(self.pane, mode)

    def deliver(self, comments: list[Comment], session_slug: str, outstanding: int | None = None) -> bool:
        """Deliver one wake covering *comments*.

        Args:
            comments: The comments triggering this wake; they identify the
                wake token and are what the ledger records.
            session_slug: Session the comments belong to.
            outstanding: Total comments awaiting a response, which is what the
                prompt reports. Defaults to the trigger count.

        Returns:
            bool: True if the wake is confirmed accepted (or already was).
        """
        ids = [comment.id for comment in comments]
        joined = " ".join(ids)
        wake_token = "tuicr-" + paths.short_hash(session_slug, joined)
        prompt = build_prompt(
            session_slug,
            self.repo_key,
            len(ids) if outstanding is None else outstanding,
            wake_token,
        )
        return self.deliver_prompt(prompt, wake_token, f"ids: {','.join(ids)}")

    def deliver_prompt(self, prompt: str, wake_token: str, label: str) -> bool:
        """Deliver an arbitrary terse tuicr lifecycle prompt.

        Args:
            prompt: Complete prompt to submit to the bound CLI.
            wake_token: Deterministic idempotency token contained in `prompt`.
            label: Human-readable event label for diagnostics.

        Returns:
            True only when the target session persisted the prompt.
        """

        if self.config.dry_run:
            print(f"--- [DRY-RUN] would wake '{self.pane or '<none>'}':")
            print(prompt)
            print("---")
            return True

        # The pane may have been reclaimed by a shell since startup; delivering
        # there would type a prompt into a command line and press Enter.
        if not copilot.pane_still_bound(self.pane, self.session):
            print(
                f"ERROR: pane '{self.pane}' no longer hosts session "
                f"'{self.session.session_id}'; not delivering.",
                file=sys.stderr,
            )
            return False

        if copilot.events_contain(self.session.events_file, wake_token):
            info(f"wake already accepted for {label} ({wake_token})")
            return True
        self._event_offset = paths.file_size(self.session.events_file)

        state = self.box_state(wake_token)
        if state != BOX_HAS_WAKE:
            tmuxio.paste(self.pane, prompt, f"tuicr-watch-{wake_token[len('tuicr-'):]}")

        mode = "csi"
        for attempt in range(1, self.config.submit_tries + 1):
            self._submit(mode)
            if self.wait_for_event(wake_token):
                self._restore_focus()
                info(f"wake accepted for {label} ({wake_token}, {mode} attempt {attempt})")
                return True

            if self.box_state(wake_token) != BOX_HAS_WAKE:
                info(
                    f"{wake_token} left the input box; waiting up to "
                    f"{self.config.queue_timeout:g}s for delayed persistence."
                )
                if self.wait_for_event(wake_token, self.config.queue_timeout):
                    self._restore_focus()
                    info(f"wake accepted for {label} ({wake_token}, queued after attempt {attempt})")
                    return True
                self._restore_focus()
                print(
                    f"ERROR: {wake_token} left the input box but was never persisted; not resubmitting.",
                    file=sys.stderr,
                )
                return False

            warn(f"{wake_token} still in the input box after {mode} attempt {attempt}; retrying.")
            # If the CLI's CSI-u keyboard encoding is unavailable, a named Enter
            # is the only other submit tmux can express. Try it before giving up.
            mode = "named"

        self._restore_focus()
        print(f"ERROR: wake not accepted for {label}.", file=sys.stderr)
        return False

    def close(self) -> None:
        """Best-effort focus restoration on shutdown."""
        try:
            self._restore_focus()
        except Exception:  # noqa: BLE001 - teardown must never mask the real exit
            pass


def _strip_prompt_marker(line: str) -> str:
    """Drop a leading ``>`` / ``❯`` prompt marker from the input box's first line."""
    stripped = line.lstrip()
    for marker in ("\u276f", ">"):
        if stripped.startswith(marker):
            return stripped[len(marker) :].lstrip()
    return line


def parse_args(argv: list[str]) -> WatchConfig:
    """Parse the command line into a :class:`WatchConfig`."""
    parser = argparse.ArgumentParser(
        prog="tuicr-watch",
        description=(
            "Watches a tuicr review session and wakes a Copilot CLI session whenever a "
            "comment is awaiting a response. Acceptance is confirmed from the target "
            "session's persisted events."
        ),
        epilog=(
            "Examples:\n"
            "  # Wake the session that owns this pane about its repo's active review:\n"
            "  tuicr-watch --repo /path/to/repo --cli-session <uuid>\n\n"
            "  # Test detection without touching tmux:\n"
            "  tuicr-watch --repo /path/to/repo --once --replay --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=".", help="Checkout used to resolve the session. Default: .")
    parser.add_argument("--session", default="", help="Session slug. Default: the single active session.")
    parser.add_argument("--cli-session", default="", help="Copilot CLI session UUID to wake.")
    parser.add_argument("--cli-pane", default="", help="tmux pane hint (e.g. %%49).")
    parser.add_argument("--interval", type=float, default=1.5, help="Poll interval. Default: 1.5")
    parser.add_argument("--submit-tries", type=int, default=3, help="Max submit attempts. Default: 3")
    parser.add_argument("--submit-step", type=float, default=0.1, help="Confirmation poll interval. Default: 0.1")
    parser.add_argument("--event-timeout", type=float, default=3.0, help="Confirmation timeout. Default: 3")
    parser.add_argument("--queue-timeout", type=float, default=300.0, help="Delayed-persistence wait. Default: 300")
    parser.add_argument("--rearm", type=float, default=900.0, help="Re-deliver after this long. 0 disables.")
    parser.add_argument("--max-attempts", type=int, default=3, help="Max deliveries per comment. Default: 3")
    parser.add_argument("--ignore-type", action="append", default=[], help="Comment type to ignore; repeatable.")
    parser.add_argument("--replay", action="store_true", help="Ignore delivery history; treat all comments as pending.")
    parser.add_argument(
        "--seed-existing",
        action="store_true",
        help="On a first run, mark the existing backlog as already-seen instead of delivering it.",
    )
    parser.add_argument("--once", action="store_true", help="Run a single fire cycle, then exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be submitted; touch nothing.")
    parser.add_argument("--state-dir", default=None, help="State dir. Default: $XDG_STATE_HOME/tuicr/watch")
    parser.add_argument("--session-state-dir", default=None, help="Copilot session-state root.")

    parsed = parser.parse_args(argv)
    return WatchConfig(
        repo=parsed.repo,
        session=parsed.session,
        cli_session=parsed.cli_session,
        cli_pane=parsed.cli_pane,
        interval=parsed.interval,
        submit_tries=parsed.submit_tries,
        submit_step=parsed.submit_step,
        event_timeout=parsed.event_timeout,
        queue_timeout=parsed.queue_timeout,
        rearm=parsed.rearm,
        max_attempts=parsed.max_attempts,
        ignore_types=parsed.ignore_type or list(tuicrio.DEFAULT_IGNORE_TYPES),
        replay=parsed.replay,
        seed_existing=parsed.seed_existing,
        once=parsed.once,
        dry_run=parsed.dry_run,
        state_dir=Path(parsed.state_dir) if parsed.state_dir else paths.state_dir("tuicr", "watch"),
        session_state_dir=Path(parsed.session_state_dir) if parsed.session_state_dir else None,
    )


def resolve_target(config: WatchConfig) -> tuple[copilot.CliSession, str]:
    """Resolve the CLI session and the tmux pane hosting it.

    Raises:
        SkillError: With exit code 3 for a missing dependency, 4 for an
            unusable tmux/session target — matching the shell contract.
    """
    require("tmux")
    if not tmuxio.inside_tmux():
        raise SkillError(
            "not inside a tmux session ($TMUX unset); cannot deliver a wake.",
            code=4,
            hint="Run inside tmux, or use --dry-run to test detection only.",
        )
    tmuxio.require_capabilities()

    pane = config.cli_pane
    session_id = config.cli_session

    if not session_id:
        pane = pane or ""
        if not pane:
            import os

            pane = os.environ.get("TMUX_PANE", "")
        if not pane:
            raise SkillError("pass --cli-session (preferred) or --cli-pane.", code=4)
        pane_pid = tmuxio.pane_pid(pane)
        if pane_pid is None:
            raise SkillError(
                f"tmux pane '{pane}' does not exist.",
                code=4,
                hint="List panes: tmux list-panes -a -F '#{pane_id} #{window_name}'",
            )
        session = copilot.session_for_pane_pid(pane_pid, config.session_state_dir)
    else:
        session = copilot.get_session(session_id, config.session_state_dir)

    if not pane:
        pane = copilot.pane_for_session(session)
    if not tmuxio.pane_exists(pane):
        raise SkillError(f"tmux pane '{pane}' does not exist.", code=4)
    if not copilot.pane_still_bound(pane, session):
        raise SkillError(
            f"Copilot session '{session.session_id}' is not running in pane '{pane}'.",
            code=4,
            hint="Omit --cli-pane to resolve the pane from the session.",
        )
    if not session.events_file.is_file():
        raise SkillError(f"Copilot session events not found: {session.events_file}", code=4)
    return session, pane


class Watcher:
    """The poll loop."""

    def __init__(self, config: WatchConfig) -> None:
        self.config = config
        self.session: copilot.CliSession | None = None
        self.pane = ""
        self.deliverer: WakeDeliverer | None = None
        self.repo_key = str(Path(config.repo).resolve()) if Path(config.repo).exists() else config.repo
        self.slug = config.session
        self.ledger: Ledger | None = None

    def _read_comments(self) -> list[Comment] | None:
        """Comments in the watched session, or None if tuicr failed.

        A tuicr failure must never look like "no comments": that would leave the
        daemon reporting healthy while silently ignoring the human forever.
        """
        try:
            return tuicrio.comments(self.config.repo, self.slug)
        except TuicrError:
            return None

    def _session_vanished(self) -> bool:
        """True once the watched session provably no longer exists.

        Distinguishes a *permanent* failure from a *transient* one, which
        ``_read_comments`` deliberately cannot: it returns None for both. The
        discriminator is that ``review list`` still answers while no longer
        naming the slug — tuicr is reachable, the session is simply gone
        (its window was closed, or the review cycle was retired without
        stopping this daemon). If ``review list`` itself fails, tuicr is down
        and the outage is transient, so we keep waiting.
        """
        try:
            return not any(s.slug == self.slug for s in tuicrio.list_sessions(self.config.repo))
        except TuicrError:
            return False

    def setup(self) -> None:
        """Resolve the target, the session slug and the state files."""
        require("tuicr")
        if not self.config.dry_run:
            self.session, self.pane = resolve_target(self.config)

        if not self.slug:
            try:
                self.slug = tuicrio.active_session(self.config.repo).slug
            except TuicrError as exc:
                raise SkillError(str(exc), code=5, hint=exc.hint) from None

        # State is keyed by repo AND slug: slugs are not unique across
        # checkouts, so hashing the slug alone lets two repos share (and
        # corrupt) one ledger.
        watch_key = paths.short_hash(self.repo_key, self.slug)
        self.ledger_file = self.config.state_dir / f"{watch_key}.json"
        self.lock_file = self.config.state_dir / f"{watch_key}.lock"
        self.ledger = Ledger(self.ledger_file, enabled=not self.config.dry_run)

        if self.session is not None:
            self.deliverer = WakeDeliverer(self.config, self.session, self.pane, self.repo_key)

    def banner(self) -> None:
        """Print the startup summary."""
        info("tuicr-watch")
        detail(f"repo:        {self.repo_key}")
        detail(f"session:     {self.slug}")
        detail(f"cli-session: {self.session.session_id if self.session else '<dry-run>'}")
        detail(f"cli-pane:    {self.pane or '<dry-run>'}")
        detail(f"interval:    {self.config.interval:g}s   ignore-types: {','.join(self.config.ignore_types)}")
        detail(f"re-arm:      {self.config.rearm:g}s (max {self.config.max_attempts} deliveries per comment)")
        detail(f"ledger:      {self.ledger_file}")
        if self.config.dry_run:
            detail("MODE: DRY-RUN (no tmux or state mutation)")
        if self.config.replay:
            detail("MODE: REPLAY (delivery history ignored)")
        if self.config.seed_existing:
            detail("MODE: SEED-EXISTING (a first-run backlog is marked seen, not delivered)")

    def seed(self) -> None:
        """Record the existing backlog as already-seen — only with ``--seed-existing``.

        Off by default. Seeding can *only* ever suppress comments that are
        genuinely unanswered, because answered ones are already excluded, so
        making it the default silently discarded live review feedback whenever
        a watcher attached to a session that already had comments — and the
        ledger is keyed by (repo, session), so every new review cycle got a
        fresh ledger and re-seeded.

        Opt in when deliberately attaching to an old session whose existing
        comments were settled out of band and should not be answered again.
        """
        assert self.ledger is not None
        if self.config.dry_run:
            info("dry-run; state will not be seeded or updated.")
            return
        if not self.config.seed_existing or self.config.replay or not self.ledger.is_empty():
            return
        comments = self._read_comments()
        if comments is None:
            return
        backlog = select_unanswered(comments, self.config)
        if backlog:
            ids = [comment.id for comment in backlog]
            self.ledger.record(ids, ids)
            info(f"seeded ledger with {len(ids)} existing comment(s).")

    def run(self) -> int:
        """Poll until stopped, or through one cycle under ``--once``."""
        assert self.ledger is not None
        # Debounce: fire only when the pending set is stable across two polls,
        # so a burst of comments produces one wake instead of one per comment.
        previous: list[str] = []
        read_failures = 0

        while True:
            comments = self._read_comments()
            if comments is None:
                read_failures += 1
                if read_failures == 1 or read_failures % _VANISH_CHECK_EVERY == 0:
                    warn(
                        f"could not read comments for session '{self.slug}' "
                        f"({read_failures} consecutive failures); not treating this as 'no comments'."
                    )
                if self.config.once:
                    return 7
                # Only after a grace period, so a tuicr restart is not mistaken
                # for the session ending. Kept off the hot path: this costs an
                # extra tuicr call and only runs while already failing.
                if read_failures >= _VANISH_CHECK_EVERY and read_failures % _VANISH_CHECK_EVERY == 0:
                    if self._session_vanished():
                        info(
                            f"session '{self.slug}' no longer exists; the review it "
                            "watched is over. Exiting."
                        )
                        return 0
                time.sleep(self.config.interval)
                continue
            read_failures = 0

            pending = select_pending(comments, self.ledger, self.config)
            ids = [comment.id for comment in pending]
            outstanding = len(select_unanswered(comments, self.config))

            if ids:
                if ids == previous:
                    info("pending: " + " | ".join(comment.summary() for comment in pending))
                    delivered = (
                        self.deliverer.deliver(pending, self.slug, outstanding)
                        if self.deliverer
                        else self._dry_deliver(pending, outstanding)
                    )
                    if delivered:
                        self.ledger.record(ids, [comment.id for comment in comments])
                    elif self.config.once:
                        return 6
                    previous = []
                    if self.config.once:
                        info("--once: exiting after one fire.")
                        return 0
                else:
                    previous = ids
            else:
                previous = []
                if self.config.once:
                    info("--once: nothing pending; exiting.")
                    return 0

            time.sleep(self.config.interval)

    def _dry_deliver(self, pending: list[Comment], outstanding: int | None = None) -> bool:
        """Print the wake that a dry run would have delivered."""
        ids = [comment.id for comment in pending]
        wake_token = "tuicr-" + paths.short_hash(self.slug, " ".join(ids))
        print(f"--- [DRY-RUN] would wake '{self.pane or '<none>'}':")
        print(
            build_prompt(
                self.slug,
                self.repo_key,
                len(ids) if outstanding is None else outstanding,
                wake_token,
            )
        )
        print("---")
        return True


def main(argv: list[str]) -> int:
    """Entry point."""
    config = parse_args(argv)
    watcher = Watcher(config)
    watcher.setup()
    watcher.banner()
    watcher.seed()

    if config.dry_run:
        return watcher.run()

    # Singleton per watched session: two daemons would double-wake the agent.
    with PidFile(watcher.lock_file):
        try:
            return watcher.run()
        finally:
            if watcher.deliverer:
                watcher.deliverer.close()


if __name__ == "__main__":
    run_main(main)
