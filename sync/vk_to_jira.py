"""One-way sync: VK tasks -> Jira issues. Status-only.

Jira is the source of truth for descriptive content (summary, description,
labels, sub-tasks) — those flow Jira -> VK in jira_to_vk.py. The reverse
direction is intentionally narrower: VK is where execution happens, so the
only thing that needs to flow back is status changes (e.g., "In Review",
"Done") so a PM watching the Jira board sees execution progress.

Identity binding: same as jira_to_vk.py — VK title `[<JIRA_KEY>] ...` encodes
the Jira issue key. VK tasks without that prefix are local-only and ignored.

Out of scope:
- Title/description edits in VK do NOT propagate back to Jira (Jira owns).
- Anti-loop dedup against Jira->VK mirror writes — engine.py handles that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from jira_client import JiraClient, JSONValue
from mcp_adapter import McpClient

from .jira_to_vk import parse_jira_key_from_title
from .state_map import jira_transition_id, normalize_vk_status, vk_status_to_jira

if TYPE_CHECKING:
    from .engine import MirrorLedger

logger = logging.getLogger(__name__)


def _extract_jira_status_name(issue: dict[str, JSONValue]) -> str:
    """Pull `fields.status.name` out of a Jira issue payload.

    Returns "" if the shape is unexpected — callers treat that as "unknown,
    skip" rather than guessing a transition target.
    """
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return ""
    status = fields.get("status")
    if not isinstance(status, dict):
        return ""
    return cast(str, status.get("name") or "")


@dataclass
class VkSyncStats:
    transitioned: int = 0
    skipped_unsupported: int = 0
    skipped_unchanged: int = 0
    skipped_orphan: int = 0
    errors: int = 0
    seen_jira_keys: list[str] = field(default_factory=list)


class VkToJiraSyncer:
    """One-way sync: VK (execution) -> Jira (status board).

    Caches `vk_id -> last_status_pushed` in memory so we don't re-fetch Jira
    when nothing changed in VK. Cache is rebuilt on process restart, which
    means after a restart the first tick will do one Jira read per non-orphan
    VK task — acceptable cost for a 30s-poll loop.
    """

    def __init__(
        self,
        jira: JiraClient,
        mcp: McpClient,
        *,
        vk_project_id: str,
        page_size: int = 100,
        ledger: "MirrorLedger | None" = None,
    ) -> None:
        self._jira = jira
        self._mcp = mcp
        self._vk_project_id = vk_project_id
        self._page_size = page_size
        self._ledger = ledger
        # vk_id -> last VK status (lowercase) we acted on
        self._last_seen_vk_status: dict[str, str] = {}

    def tick(self) -> VkSyncStats:
        """One pass: pull VK, diff against Jira, push transitions. Idempotent."""
        stats = VkSyncStats()

        try:
            vk_issues = self._mcp.list_all_issues(
                self._vk_project_id, page_size=self._page_size
            )
        except Exception as e:
            logger.error("VK list failed for project %s: %s", self._vk_project_id, e)
            stats.errors += 1
            return stats

        for vk in vk_issues:
            jira_key, _ = parse_jira_key_from_title(vk.title)
            if not jira_key:
                stats.skipped_orphan += 1
                continue
            stats.seen_jira_keys.append(jira_key)

            # Canonical compact form ('inprogress'); tolerates VK 0.1.43's
            # display wire format ('In progress') as well as the lowercase
            # Rust enum name ('inprogress') and any test-fixture variant.
            vk_status_lc = normalize_vk_status(vk.status or "")
            target_jira_status = vk_status_to_jira(vk_status_lc)
            if target_jira_status is None:
                # VK has a status we don't know how to translate. Don't guess.
                logger.warning(
                    "VK task %s (jira=%s) has unmappable status %r",
                    vk.id, jira_key, vk.status,
                )
                stats.skipped_unsupported += 1
                continue

            # Fast path: VK status hasn't changed since our last successful push,
            # so Jira is already up to date (modulo external Jira edits, which
            # the next Jira->VK tick will pick up anyway).
            previous = self._last_seen_vk_status.get(vk.id)
            if previous == vk_status_lc:
                stats.skipped_unchanged += 1
                continue

            # Anti-loop: if Jira->VK just pushed this exact VK status, what
            # we're seeing on the VK side is our own echo. Don't bounce it
            # back to Jira (Jira already has the corresponding state).
            if (
                self._ledger is not None
                and self._ledger.vk_status_was_pushed(jira_key, vk_status_lc)
            ):
                self._last_seen_vk_status[vk.id] = vk_status_lc
                stats.skipped_unchanged += 1
                continue

            try:
                jira_issue = self._jira.get_issue(jira_key)
            except Exception as e:
                logger.warning("get_issue %s failed: %s", jira_key, e)
                stats.errors += 1
                continue

            current_jira_status = _extract_jira_status_name(jira_issue)
            if current_jira_status == target_jira_status:
                # Already in sync — likely we mirrored Jira -> VK on a previous
                # tick and now VK is reflecting the same state back. Cache
                # this so we don't re-read Jira on the next tick.
                self._last_seen_vk_status[vk.id] = vk_status_lc
                stats.skipped_unchanged += 1
                continue

            transition_id = jira_transition_id(target_jira_status)
            if transition_id is None:
                # No workflow transition lands on the target status (e.g. the
                # SCRUM workflow has no Cancelled transition). Skip rather
                # than fail — operator can configure a transition later.
                logger.warning(
                    "No Jira transition for target status %r (vk=%s, jira=%s)",
                    target_jira_status, vk.id, jira_key,
                )
                stats.skipped_unsupported += 1
                continue

            try:
                self._jira.transition_issue(jira_key, transition_id)
            except Exception as e:
                logger.error(
                    "transition %s -> %s (id=%s) failed: %s",
                    jira_key, target_jira_status, transition_id, e,
                )
                stats.errors += 1
                continue

            self._last_seen_vk_status[vk.id] = vk_status_lc
            if self._ledger is not None:
                self._ledger.record_pushed_to_jira(jira_key, target_jira_status)
            stats.transitioned += 1
            logger.info(
                "VK->Jira: %s %s -> %s",
                jira_key, current_jira_status or "?", target_jira_status,
            )

        return stats
