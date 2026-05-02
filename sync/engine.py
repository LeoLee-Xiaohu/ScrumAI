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
from dataclasses import dataclass

from jira_client import JiraClient
from mcp_adapter import McpClient

from .jira_to_vk import JiraToVkSyncer
from .vk_to_jira import VkToJiraSyncer

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
    """Owns both syncers + ledger; drives a polling loop with graceful shutdown."""

    def __init__(
        self,
        jira: JiraClient,
        mcp: McpClient,
        *,
        jira_project_key: str,
        vk_project_id: str,
        interval_seconds: float = 30.0,
        ledger_ttl_seconds: float = 90.0,
    ) -> None:
        self._jira = jira
        self._mcp = mcp
        self._interval = interval_seconds
        self._ledger = MirrorLedger(ttl_seconds=ledger_ttl_seconds)
        self._stop_event = threading.Event()

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
        )

    @property
    def ledger(self) -> MirrorLedger:
        """Exposed for tests."""
        return self._ledger

    def stop(self) -> None:
        """Signal the run_loop to exit at the next iteration boundary."""
        self._stop_event.set()

    def tick(self) -> TickReport:
        """Run one Jira->VK + VK->Jira pass. Order is intentional.

        We run Jira->VK first because Jira is the source of truth for new
        issues and content edits — getting VK in sync first means the
        subsequent VK->Jira pass sees fresh VK state and won't push stale
        deltas backwards.
        """
        report = TickReport()

        try:
            j2v = self._jira_to_vk.tick()
            report.jira_to_vk_created = j2v.created
            report.jira_to_vk_updated = j2v.updated_status + j2v.updated_content
            report.jira_to_vk_skipped = j2v.skipped_unchanged + j2v.skipped_unsupported
            report.jira_to_vk_errors = j2v.errors
        except Exception as e:
            logger.exception("Jira->VK tick crashed: %s", e)
            report.jira_to_vk_errors += 1

        try:
            v2j = self._vk_to_jira.tick()
            report.vk_to_jira_transitioned = v2j.transitioned
            report.vk_to_jira_skipped = (
                v2j.skipped_unchanged + v2j.skipped_unsupported + v2j.skipped_orphan
            )
            report.vk_to_jira_errors = v2j.errors
        except Exception as e:
            logger.exception("VK->Jira tick crashed: %s", e)
            report.vk_to_jira_errors += 1

        return report

    def run_loop(self) -> None:
        """Tick forever (or until `stop()`), respecting the configured interval.

        Installs SIGINT/SIGTERM handlers if running on the main thread so
        Ctrl+C and `kill` cleanly drain the current tick before exiting.
        """
        self._install_signal_handlers()

        logger.info(
            "sync engine started: interval=%.1fs project=%s",
            self._interval,
            self._jira_to_vk._jira_project_key,
        )

        try:
            while not self._stop_event.is_set():
                start = time.monotonic()
                report = self.tick()
                self._log_tick(report)

                # Subtract the actual tick duration so a slow tick doesn't
                # snowball: aim for the next tick at start+interval, not
                # at "now+interval".
                elapsed = time.monotonic() - start
                sleep_for = max(0.0, self._interval - elapsed)
                if sleep_for == 0.0 and self._interval > 0:
                    logger.warning(
                        "tick exceeded interval (%.1fs > %.1fs); running back-to-back",
                        elapsed, self._interval,
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
