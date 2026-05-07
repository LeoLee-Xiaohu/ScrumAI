"""Sync engine: drives JiraToVk and VkToJira on a polling loop.

Anti-loop strategy:
- Both syncers naturally avoid loops in steady state because each one re-reads
  the *current* state on the other side before writing. So Jira "In Progress"
  -> push to VK -> VK reports "inprogress" -> VkToJira reads Jira -> already
  "In Progress" -> no-op.
- The exception is partial-write races: e.g. JiraToVk creates a VK task in
  default `todo` and the follow-up `update_issue(status=inprogress)` fails.
  In between ticks, VkToJira might see VK="todo" while Jira="In Progress" and
  push Jira backwards to "To Do".
- MirrorLedger guards against that: after each successful write, the
  source-of-write records "I just pushed value V on side S for key K". The
  opposite syncer consults the ledger before writing and treats a recent
  matching entry as "this came from us, skip the echo".

The ledger is short-lived (90s default = 3 polling intervals at 30s) and
in-memory only — restart-safe by design, since any stale state will be
re-discovered on the next tick.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from jira_client import JiraClient
from mcp_adapter import McpClient

from .jira_to_vk import JiraToVkSyncer, SyncStats
from .vk_to_jira import VkSyncStats, VkToJiraSyncer

logger = logging.getLogger(__name__)


class MirrorLedger:
    """Time-bounded record of writes each side has made to the other.

    Entries are keyed by (side, jira_key), where `side` is "vk" if the entry
    records a write *to* VK (made by JiraToVk) or "jira" if it records a
    write *to* Jira (made by VkToJira). The opposite syncer reads its
    counterpart's records to decide whether an observed delta is its own
    echo or a genuine new change.
    """

    def __init__(self, ttl_seconds: float = 90.0) -> None:
        self._ttl = ttl_seconds
        # (side, jira_key) -> (status_value, expires_at_monotonic)
        self._entries: dict[tuple[str, str], tuple[str, float]] = {}
        self._lock = threading.Lock()

    def record_pushed_to_vk(self, jira_key: str, vk_status: str) -> None:
        with self._lock:
            self._entries[("vk", jira_key)] = (
                vk_status.lower(),
                time.monotonic() + self._ttl,
            )

    def record_pushed_to_jira(self, jira_key: str, jira_status: str) -> None:
        with self._lock:
            self._entries[("jira", jira_key)] = (
                jira_status,
                time.monotonic() + self._ttl,
            )

    def vk_status_was_pushed(self, jira_key: str, vk_status: str) -> bool:
        return self._matches(("vk", jira_key), vk_status.lower())

    def jira_status_was_pushed(self, jira_key: str, jira_status: str) -> bool:
        return self._matches(("jira", jira_key), jira_status)

    def _matches(self, key: tuple[str, str], value: str) -> bool:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            recorded_value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._entries[key]
                return False
            return recorded_value == value


@dataclass
class TickReport:
    """Combined per-tick stats — what each direction did."""

    jira_to_vk_created: int = 0
    jira_to_vk_updated: int = 0
    jira_to_vk_skipped: int = 0
    jira_to_vk_errors: int = 0
    vk_to_jira_transitioned: int = 0
    vk_to_jira_skipped: int = 0
    vk_to_jira_errors: int = 0

    def total_writes(self) -> int:
        return (
            self.jira_to_vk_created
            + self.jira_to_vk_updated
            + self.vk_to_jira_transitioned
        )


class SyncEngine:
    """Owns both syncers + ledger; drives a polling loop with graceful shutdown.

    Adaptive polling: when a write happened within `hot_window_seconds`, the
    next sleep is `hot_interval_seconds` (default 30s). Past that, polling
    drops to `cold_interval_seconds` (default 300s). Both polling ticks and
    HTTP-triggered targeted ticks (`tick_for_key`) update the write
    timestamp, so a Forge-driven status change keeps the loop responsive
    for an hour, then quiets down. Set `hot_interval == cold_interval` to
    pin a fixed cadence (used in tests and in `--once` mode).
    """

    def __init__(
        self,
        jira: JiraClient,
        mcp: McpClient,
        *,
        jira_project_key: str,
        vk_project_id: str,
        interval_seconds: float = 30.0,
        ledger_ttl_seconds: float = 90.0,
        hot_interval_seconds: float | None = None,
        cold_interval_seconds: float | None = None,
        hot_window_seconds: float = 3600.0,
    ) -> None:
        self._jira = jira
        self._mcp = mcp
        # `interval_seconds` is the legacy fixed cadence; default both hot
        # and cold to it when adaptive params aren't passed, so existing
        # call sites and tests keep their old behavior.
        self._hot_interval = (
            hot_interval_seconds if hot_interval_seconds is not None else interval_seconds
        )
        self._cold_interval = (
            cold_interval_seconds if cold_interval_seconds is not None else interval_seconds
        )
        self._hot_window = hot_window_seconds
        self._ledger = MirrorLedger(ttl_seconds=ledger_ttl_seconds)
        self._stop_event = threading.Event()
        # Monotonic timestamp of the last tick that produced any write.
        # `None` means "never written this process" — that counts as cold.
        self._last_write_at: float | None = None

        self._jira_to_vk = JiraToVkSyncer(
            jira=jira,
            mcp=mcp,
            jira_project_key=jira_project_key,
            vk_project_id=vk_project_id,
            ledger=self._ledger,
        )
        self._vk_to_jira = VkToJiraSyncer(
            jira=jira,
            mcp=mcp,
            vk_project_id=vk_project_id,
            ledger=self._ledger,
            jira_project_key=jira_project_key,
        )

    @property
    def ledger(self) -> MirrorLedger:
        """Exposed for tests."""
        return self._ledger

    @property
    def last_write_at(self) -> float | None:
        """Monotonic time of the last write, or None if nothing has been
        written yet this process lifetime. Exposed for tests + observability.
        """
        return self._last_write_at

    def next_interval_seconds(self) -> float:
        """How long the polling loop should sleep before the next tick.

        Hot when we wrote within `hot_window_seconds`, cold otherwise.
        Cold start (no writes yet) is treated as cold — there's no recent
        evidence the system is busy, so don't spam Jira/VK.
        """
        if self._last_write_at is None:
            return self._cold_interval
        age = time.monotonic() - self._last_write_at
        return self._hot_interval if age < self._hot_window else self._cold_interval

    def stop(self) -> None:
        """Signal the run_loop to exit at the next iteration boundary."""
        self._stop_event.set()

    def _run_directional(
        self,
        report: TickReport,
        *,
        j2v_call: Callable[[], SyncStats],
        v2j_call: Callable[[], VkSyncStats],
    ) -> TickReport:
        """Run both syncer halves with shared error wrapping.

        Used by `tick()` (full sweep) and `tick_for_key()` (targeted) — the
        only difference between them is which method on each syncer is
        invoked, so we factor that out to a callable.
        """
        try:
            j2v = j2v_call()
            report.jira_to_vk_created = j2v.created
            report.jira_to_vk_updated = j2v.updated_status + j2v.updated_content
            report.jira_to_vk_skipped = j2v.skipped_unchanged + j2v.skipped_unsupported
            report.jira_to_vk_errors = j2v.errors
        except Exception as e:
            logger.exception("Jira->VK tick crashed: %s", e)
            report.jira_to_vk_errors += 1

        try:
            v2j = v2j_call()
            report.vk_to_jira_transitioned = v2j.transitioned
            report.vk_to_jira_skipped = (
                v2j.skipped_unchanged + v2j.skipped_unsupported + v2j.skipped_orphan
            )
            report.vk_to_jira_errors = v2j.errors
        except Exception as e:
            logger.exception("VK->Jira tick crashed: %s", e)
            report.vk_to_jira_errors += 1

        if report.total_writes() > 0:
            self._last_write_at = time.monotonic()

        return report

    def tick(self) -> TickReport:
        """Run one Jira->VK + VK->Jira full sweep. Order is intentional.

        We run Jira->VK first because Jira is the source of truth for new
        issues and content edits — getting VK in sync first means the
        subsequent VK->Jira pass sees fresh VK state and won't push stale
        deltas backwards.
        """
        return self._run_directional(
            TickReport(),
            j2v_call=self._jira_to_vk.tick,
            v2j_call=self._vk_to_jira.tick,
        )

    def tick_for_key(self, jira_key: str) -> TickReport:
        """Run a targeted sync for a single Jira key.

        Same Jira->VK then VK->Jira ordering as `tick()`, but each side only
        touches the requested key. Used by the HTTP API to give Forge a
        sub-second sync after a user action — much cheaper than a full
        project scan, and Jira `get_issue` sees freshly-changed state with
        no `/search/jql` indexing lag.
        """
        return self._run_directional(
            TickReport(),
            j2v_call=lambda: self._jira_to_vk.tick_for_key(jira_key),
            v2j_call=lambda: self._vk_to_jira.tick_for_key(jira_key),
        )

    def run_loop(self) -> None:
        """Tick forever (or until `stop()`), with adaptive interval.

        Installs SIGINT/SIGTERM handlers if running on the main thread so
        Ctrl+C and `kill` cleanly drain the current tick before exiting.
        """
        self._install_signal_handlers()

        logger.info(
            "sync engine started: hot=%.1fs cold=%.1fs window=%.1fs project=%s",
            self._hot_interval,
            self._cold_interval,
            self._hot_window,
            self._jira_to_vk._jira_project_key,
        )

        try:
            while not self._stop_event.is_set():
                start = time.monotonic()
                report = self.tick()
                self._log_tick(report)

                interval = self.next_interval_seconds()
                # Subtract the actual tick duration so a slow tick doesn't
                # snowball: aim for the next tick at start+interval, not
                # at "now+interval".
                elapsed = time.monotonic() - start
                sleep_for = max(0.0, interval - elapsed)
                if sleep_for == 0.0 and interval > 0:
                    logger.warning(
                        "tick exceeded interval (%.1fs > %.1fs); running back-to-back",
                        elapsed, interval,
                    )
                # Use Event.wait so stop() interrupts mid-sleep.
                if self._stop_event.wait(timeout=sleep_for):
                    break
        finally:
            logger.info("sync engine stopped")

    def _install_signal_handlers(self) -> None:
        # signal.signal only works on the main thread; in tests we drive
        # tick() directly so the absence of handlers is fine.
        if threading.current_thread() is not threading.main_thread():
            return

        def handler(signum: int, _frame: object) -> None:
            logger.info("received signal %d; shutting down after current tick", signum)
            self._stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                # Already handled, or running in an env that disallows it.
                pass

    @staticmethod
    def _log_tick(report: TickReport) -> None:
        if report.total_writes() == 0 and (
            report.jira_to_vk_errors == 0 and report.vk_to_jira_errors == 0
        ):
            # Quiet steady-state ticks. Operators tail the log at INFO; debug
            # users can flip to DEBUG for full visibility.
            logger.debug(
                "tick idle: j2v_skipped=%d v2j_skipped=%d",
                report.jira_to_vk_skipped, report.vk_to_jira_skipped,
            )
            return

        logger.info(
            "tick: j2v(created=%d updated=%d errors=%d) v2j(transitioned=%d errors=%d)",
            report.jira_to_vk_created,
            report.jira_to_vk_updated,
            report.jira_to_vk_errors,
            report.vk_to_jira_transitioned,
            report.vk_to_jira_errors,
        )
