"""One-way sync: Jira issues -> VK tasks. Jira is the source of truth.

Identity binding: VK title is `[<JIRA_KEY>] <jira summary>`. The MCP
`create_issue`/`update_issue` schemas don't expose VK's own `external_id`
column (the field exists in `crates/db/src/models/task.rs` but isn't part of
the JSON-RPC surface as of vibe-kanban 0.1.43), so we encode the Jira key in
the title — visible, searchable, and matching the `[story_id]`-prefix
convention already used by `run_mcp_export` in mcp_adapter.py.

Scope:
- Reads Jira (`/rest/api/3/search/jql`) and VK (`list_issues` MCP tool).
- For each syncable Jira issue, creates or updates a VK task.
- Skips Jira `Backlog` (no VK equivalent — see state_map.is_syncable_jira_status).

Out of scope here:
- VK -> Jira sync (see vk_to_jira.py).
- Anti-loop dedup against VK->Jira mirror writes — that lives in engine.py.
- Sub-issue parent linking — possible via MCP `parent_issue_id` but deferred
  until we have a real Jira sub-task to test against.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from jira_client import JiraClient, JSONValue
from mcp_adapter import McpClient, McpIssue

from .state_map import (
    is_syncable_jira_status,
    jira_status_to_vk,
    normalize_vk_status,
    vk_status_to_display,
)

if TYPE_CHECKING:
    from .engine import MirrorLedger

logger = logging.getLogger(__name__)

# `[KEY-NN] <rest>` — first capture is the Jira-style issue key.
# Lax on the project prefix so SCRUM-27, PROJ-1, ABC_DEF-12 all parse.
_TITLE_KEY_RE = re.compile(r"^\[([A-Z][A-Z0-9_]*-\d+)\]\s+(.*)$")


def parse_jira_key_from_title(title: str) -> tuple[str | None, str]:
    """Pull a Jira-style key from a VK title prefix.

    Returns (jira_key, remaining_title). For titles that don't match the
    `[KEY-NN] ...` convention, returns (None, original_title) — those are
    locally-created VK tasks we should leave alone.
    """
    m = _TITLE_KEY_RE.match(title or "")
    if not m:
        return None, title or ""
    return m.group(1), m.group(2)


def make_vk_title(jira_key: str, jira_summary: str) -> str:
    return f"[{jira_key}] {jira_summary}"


def _adf_to_plain(node: JSONValue) -> str:
    """Best-effort flatten of an Atlassian Document Format tree to plain text.

    ADF is a recursive tree; we only care about preserving readable content
    for the VK description, so we walk it and concatenate `text` nodes,
    inserting newlines at block-level boundaries.
    """
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    if node_type == "text":
        return cast(str, node.get("text") or "")

    content = node.get("content")
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for child in content:
        text = _adf_to_plain(cast(JSONValue, child))
        if text:
            parts.append(text)

    block_types = {"doc", "paragraph", "heading", "bulletList", "orderedList", "listItem"}
    sep = "\n" if node_type in block_types else ""
    return sep.join(parts) if sep else "".join(parts)


@dataclass
class JiraIssueSnapshot:
    """The fields we actually sync — built once per Jira issue per tick."""

    key: str
    summary: str
    status_name: str
    description_text: str

    @classmethod
    def from_search_hit(cls, raw: dict[str, JSONValue]) -> "JiraIssueSnapshot":
        key = cast(str, raw.get("key") or "")
        fields_obj = raw.get("fields")
        if not isinstance(fields_obj, dict):
            return cls(key=key, summary="", status_name="", description_text="")

        summary = cast(str, fields_obj.get("summary") or "")

        status = fields_obj.get("status")
        status_name = ""
        if isinstance(status, dict):
            status_name = cast(str, status.get("name") or "")

        desc_node = fields_obj.get("description")
        desc_text = _adf_to_plain(cast(JSONValue, desc_node)) if desc_node else ""

        return cls(
            key=key,
            summary=summary,
            status_name=status_name,
            description_text=desc_text,
        )

    def signature(self) -> tuple[str, str]:
        """Tuple used for "did anything change since last tick" comparisons.

        Status is intentionally excluded — it's tracked separately as
        `status_drift` in the syncer, and including it here would make
        content_drift fire on every status change (double-counting).
        """
        return (self.summary, self.description_text)


@dataclass
class SyncStats:
    created: int = 0
    updated_status: int = 0
    updated_content: int = 0
    skipped_unsupported: int = 0
    skipped_unchanged: int = 0
    errors: int = 0
    seen_jira_keys: list[str] = field(default_factory=list)

    def writes(self) -> int:
        return self.created + self.updated_status + self.updated_content


@dataclass(frozen=True)
class VkIssueSnapshot:
    """Small, Forge-friendly view of a VK card bound to a Jira issue key."""

    jira_key: str
    id: str
    simple_id: str
    title: str
    status: str
    priority: str | None = None


class VkBackendUnavailableError(RuntimeError):
    """Raised when a VK list call signals transport failure.

    Two failure modes both surface as this error so callers (Forge's issue
    panel) can distinguish "VK is temporarily silent" from "card doesn't
    exist":
    1. The MCP list call raises (network error, MCP crash).
    2. vibe-kanban's MCP server returns a well-formed empty issues list
       when its backend is unreachable, after a previous tick observed
       N>0 bound tasks — same signature `tick()` uses.
    """


class JiraToVkSyncer:
    """Stateless-ish sync: Jira (truth) -> VK (mirror).

    Holds an in-memory `_last_seen_jira` cache keyed by Jira issue key, so we
    don't PATCH VK on every tick when Jira hasn't changed. The cache is
    populated lazily and survives across ticks within the same process; on
    restart we recover by treating "first observation == authoritative" and
    only patching when fields actually drift from VK's current state.
    """

    def __init__(
        self,
        jira: JiraClient,
        mcp: McpClient,
        *,
        jira_project_key: str,
        vk_project_id: str,
        page_size: int = 100,
        ledger: "MirrorLedger | None" = None,
    ) -> None:
        self._jira = jira
        self._mcp = mcp
        self._jira_project_key = jira_project_key
        self._vk_project_id = vk_project_id
        self._page_size = page_size
        self._ledger = ledger
        # jira_key -> last-observed (summary, description) tuple
        self._last_seen_jira: dict[str, tuple[str, str]] = {}
        # jira_key -> last-observed Jira status name (e.g. "In Progress").
        # Tracked separately from signature so a status change doesn't
        # double-count as content_drift, and so we can tell "Jira changed"
        # from "VK drifted" when the two sides disagree.
        self._last_seen_jira_status: dict[str, str] = {}
        # Last tick's count of VK tasks bound to a Jira key. Used to detect
        # "VK silently returned empty" — vibe-kanban's MCP server, when its
        # backend is unreachable (e.g. SSH tunnel down), responds with a
        # well-formed empty issues list rather than an error. Without this
        # guard, the syncer would think every Jira issue is new and try to
        # mass-recreate them. Cold start (count=0) lets legitimate first
        # runs through. Invariant: NOT reset when the guard aborts a tick —
        # we keep the previous high-water mark so the next tick re-evaluates
        # against the same count and an isolated MCP blip can't shift the
        # bar to 0. Only successful ticks update it (see end of tick()).
        # Known low-severity false positive: a project legitimately drained
        # to 0 bound tasks AND immediately followed by transport failure
        # would also abort here. Operator can clear by restarting the
        # process; we'd rather over-trigger than mass-recreate.
        self._last_vk_bound_count: int = 0

    # ----- diffing helpers -----

    def _index_vk_by_key(self, vk_issues: list[McpIssue]) -> dict[str, McpIssue]:
        """Build {jira_key -> McpIssue} from VK tasks whose title encodes a key."""
        index: dict[str, McpIssue] = {}
        for issue in vk_issues:
            key, _ = parse_jira_key_from_title(issue.title)
            if not key:
                continue
            if key in index:
                logger.warning(
                    "Multiple VK tasks share Jira key %s (first=%s, dup=%s); using first",
                    key, index[key].id, issue.id,
                )
                continue
            index[key] = issue
        return index

    def get_vk_issue_for_key(self, jira_key: str) -> VkIssueSnapshot | None:
        """Return the live VK card snapshot bound to `jira_key`, if present.

        This is a read-only helper for the Forge panel. It deliberately uses
        the same title-prefix binding as the sync path, so the UI only shows
        cards that actually match the Jira key instead of rendering whatever
        the VK workspace endpoint happens to return.

        Raises `VkBackendUnavailableError` for two transport-failure modes:
        an exception from `list_all_issues` (network/MCP crash), or a
        bound-task count of 0 after a previous `tick()` saw N>0 — same
        signature `tick()` uses. Cold start (`_last_vk_bound_count == 0`)
        still returns None on miss, matching `tick()`'s cold-start contract.
        """
        if not jira_key.startswith(self._jira_project_key + "-"):
            logger.warning(
                "get_vk_issue_for_key refused: key %r is outside configured project %r",
                jira_key,
                self._jira_project_key,
            )
            return None

        try:
            vk_issues = self._mcp.list_all_issues(
                self._vk_project_id,
                page_size=self._page_size,
            )
        except Exception as e:
            # Mirror tick()'s defensive posture: an MCP exception is the
            # other half of "VK is unreachable". Without this, a network
            # error would propagate as 500 and Forge's transport-failure
            # branch would still render "no mirror" — same duplicate-create
            # risk as the silent-empty path.
            raise VkBackendUnavailableError(
                f"VK list call failed: {e}"
            ) from e

        vk_by_key = self._index_vk_by_key(vk_issues)

        # Same guard as tick(): VK MCP responds with a well-formed empty
        # issues list when its backend is unreachable. Returning None here
        # would tell Forge "no mirror exists" and could trigger a duplicate
        # mirror create on the next user action. On cold start we have no
        # baseline, so we let it through (consistent with tick()).
        if not vk_by_key and self._last_vk_bound_count > 0:
            raise VkBackendUnavailableError(
                f"VK list returned 0 bound tasks but {self._last_vk_bound_count} "
                f"were seen last tick — treating as transient MCP/VK failure"
            )

        issue = vk_by_key.get(jira_key)
        if issue is None:
            return None

        return VkIssueSnapshot(
            jira_key=jira_key,
            id=issue.id,
            simple_id=issue.simple_id,
            title=issue.title,
            status=issue.status,
            priority=issue.priority,
        )

    # ----- per-issue handlers -----

    def _create_vk(self, snap: JiraIssueSnapshot, target_vk_status: str) -> bool:
        """Create a fresh VK task. New issues land in `todo`; we transition after.

        Returns False if either the create OR the post-create status update
        fails. The partial-failure case (issue created, status set failed)
        is intentionally surfaced as an error so the caller skips caching
        Jira state — the next tick re-enters the cold-start path
        (existing != None, previous_jira_status is None ⇒ Jira wins) and
        reconciles the stranded `todo` status.

        Status updates use the display wire form ('In progress' not
        'inprogress') because vibe-kanban 0.1.43's `update_issue` silently
        no-ops on the compact form — see state_map.vk_status_to_display.
        """
        title = make_vk_title(snap.key, snap.summary)
        vk_id = self._mcp.create_issue(
            project_id=self._vk_project_id,
            title=title,
            description=snap.description_text or None,
        )
        if not vk_id:
            logger.error("create_issue failed for %s", snap.key)
            return False

        if target_vk_status != "todo":
            wire_status = vk_status_to_display(target_vk_status) or target_vk_status
            ok = self._mcp.update_issue(issue_id=vk_id, status=wire_status)
            if not ok:
                logger.error(
                    "Created VK task for %s but failed to set status %s; "
                    "next tick will retry via cold-start",
                    snap.key, target_vk_status,
                )
                return False
        return True

    def _update_vk(
        self,
        existing: McpIssue,
        snap: JiraIssueSnapshot,
        target_vk_status: str,
        previous_signature: tuple[str, str] | None,
        previous_jira_status: str | None,
    ) -> tuple[bool, bool] | None:
        """Patch a VK task to match Jira state.

        Returns:
            (status_changed, content_changed) on success or no-op,
            None if the MCP `update_issue` call itself failed — caller
            must NOT cache Jira state in that case so the next tick
            retries against the same drift.

        Strategy:
        - Status: compare existing.status (lowercase) to target. With
          history, we only push when Jira's status actually moved since
          last tick — a steady Jira side disagreeing with VK means VK is
          the one that drifted (likely an operator move), and VK->Jira
          will propagate it. On cold start (no history), Jira wins.
        - Title: we have it from list_issues, compare directly.
        - Description: list_issues doesn't return it, so we only patch when
          we've seen this Jira key before AND its content changed since then.
          On cold start (previous=None) we skip description to avoid
          churning every VK task on the first tick.

        Cold-start contract: when Jira and VK disagree on status with no
        prior history, *Jira wins*. Jira is the source of truth for
        descriptive state and the brainstorm/decomposition flow; if VK
        drifted independently while sync was offline, the first tick after
        sync resumes pulls VK back into alignment. VK->Jira sync re-applies
        any genuine VK-side changes that happen *after* this initial reset.
        """
        raw_status_drift = normalize_vk_status(existing.status) != target_vk_status
        if previous_jira_status is None:
            # Cold start: Jira wins — clobber any VK drift to canonical Jira state.
            status_drift = raw_status_drift
        else:
            # Established baseline: only push when Jira actually moved.
            # If Jira's status is unchanged from what we saw last tick but
            # VK now disagrees, that means VK is the one that moved — leave
            # it for VK->Jira to propagate.
            jira_status_changed = previous_jira_status != snap.status_name
            status_drift = raw_status_drift and jira_status_changed
        desired_title = make_vk_title(snap.key, snap.summary)
        title_drift = existing.title != desired_title

        current = snap.signature()
        content_drift = previous_signature is not None and previous_signature != current

        if not (status_drift or title_drift or content_drift):
            return (False, False)

        wire_status: str | None = None
        if status_drift:
            wire_status = vk_status_to_display(target_vk_status) or target_vk_status
        ok = self._mcp.update_issue(
            issue_id=existing.id,
            status=wire_status,
            title=desired_title if title_drift else None,
            description=snap.description_text if content_drift else None,
        )
        if not ok:
            logger.error("update_issue failed for %s (vk_id=%s)", snap.key, existing.id)
            return None

        return (status_drift, title_drift or content_drift)

    # ----- public driver -----

    def _sync_one(
        self,
        snap: JiraIssueSnapshot,
        vk_by_key: dict[str, McpIssue],
        stats: SyncStats,
    ) -> None:
        """Apply create/update/skip logic for a single Jira issue.

        Shared between full-sweep `tick()` and targeted `tick_for_key()`.
        Mutates `stats` and the in-memory caches; the caller is responsible
        for fetching `snap` and providing the VK index.
        """
        if not snap.key:
            return
        stats.seen_jira_keys.append(snap.key)

        if not is_syncable_jira_status(snap.status_name):
            # Backlog or unmapped — leave alone. Don't cache, so a later
            # transition out of Backlog re-enters the create path cleanly.
            stats.skipped_unsupported += 1
            return

        target_vk_status = jira_status_to_vk(snap.status_name)
        if target_vk_status is None:
            # Defensive: is_syncable_jira_status already gates this, but
            # if state_map is mid-edit we don't want to crash.
            stats.skipped_unsupported += 1
            return

        existing = vk_by_key.get(snap.key)
        previous_sig = self._last_seen_jira.get(snap.key)
        previous_status = self._last_seen_jira_status.get(snap.key)
        current_sig = snap.signature()

        # Anti-loop: if we just transitioned Jira from VK->Jira, the
        # status we're now reading on the Jira side is our own echo.
        # Don't bounce it back through to VK — VK already has that state.
        if (
            self._ledger is not None
            and existing is not None
            and self._ledger.jira_status_was_pushed(snap.key, snap.status_name)
            and normalize_vk_status(existing.status) == target_vk_status
        ):
            stats.skipped_unchanged += 1
            self._last_seen_jira[snap.key] = current_sig
            self._last_seen_jira_status[snap.key] = snap.status_name
            return

        if existing is None:
            if self._create_vk(snap, target_vk_status):
                stats.created += 1
                if self._ledger is not None:
                    self._ledger.record_pushed_to_vk(snap.key, target_vk_status)
                # Cache only on full success: a partial-create failure
                # (issue created but status update failed) returns False
                # and we want the next tick's cold-start path to reconcile.
                self._last_seen_jira[snap.key] = current_sig
                self._last_seen_jira_status[snap.key] = snap.status_name
            else:
                stats.errors += 1
            return

        result = self._update_vk(
            existing, snap, target_vk_status, previous_sig, previous_status
        )
        if result is None:
            # MCP write failed — count as error and skip caching so the
            # next tick retries the same drift instead of falsely
            # treating Jira state as already mirrored.
            stats.errors += 1
            return

        status_changed, content_changed = result
        if status_changed:
            stats.updated_status += 1
        if content_changed:
            stats.updated_content += 1
        if not (status_changed or content_changed):
            stats.skipped_unchanged += 1
        elif self._ledger is not None:
            # Only record on actual writes; "no-op" patches don't move state.
            self._ledger.record_pushed_to_vk(snap.key, target_vk_status)

        self._last_seen_jira[snap.key] = current_sig
        self._last_seen_jira_status[snap.key] = snap.status_name

    def tick(self) -> SyncStats:
        """One pass: pull Jira, diff against VK, push deltas. Idempotent."""
        stats = SyncStats()

        try:
            raw_issues = self._jira.search_issues_by_project(
                self._jira_project_key, limit=self._page_size
            )
        except Exception as e:
            logger.error("Jira search failed for %s: %s", self._jira_project_key, e)
            stats.errors += 1
            return stats

        try:
            vk_issues = self._mcp.list_all_issues(
                self._vk_project_id, page_size=self._page_size
            )
        except Exception as e:
            logger.error("VK list failed for project %s: %s", self._vk_project_id, e)
            stats.errors += 1
            return stats

        vk_by_key = self._index_vk_by_key(vk_issues)

        # Sanity guard: VK MCP returns a successful empty list when its
        # backend is unreachable. If we previously saw bound tasks and now
        # see none, that's transient connectivity loss — not a wiped
        # project. Abort before mass-recreating Jira mirrors. On cold start
        # (_last_vk_bound_count == 0) we proceed normally.
        if not vk_by_key and self._last_vk_bound_count > 0:
            logger.error(
                "VK list returned 0 bound tasks but %d were seen last tick — "
                "treating as transient MCP/VK failure, skipping create path",
                self._last_vk_bound_count,
            )
            stats.errors += 1
            return stats

        for raw in raw_issues:
            snap = JiraIssueSnapshot.from_search_hit(raw)
            self._sync_one(snap, vk_by_key, stats)

        # Update the bound-count high-water mark only on a tick that actually
        # observed VK successfully. Decreasing toward 0 is fine (deletions
        # happen) but going from N>0 to 0 in a single tick is the signature
        # we guard against above.
        self._last_vk_bound_count = len(vk_by_key)

        return stats

    def tick_for_key(self, jira_key: str) -> SyncStats:
        """Targeted sync for a single Jira key.

        Used by the HTTP API for Forge-triggered immediate sync. Trades the
        ~3s indexing lag of `/search/jql` for a direct GET on the issue
        (`/rest/api/3/issue/{key}`), so the freshly-changed Jira state is
        visible immediately. Still has to list VK to find the matching task —
        VK 0.1.43 has no `find by external_id` API, so we filter the title
        prefix client-side.

        The empty-VK guard is intentionally NOT applied here: a single-key
        request can't meaningfully distinguish "VK is healthy but this key
        has no mirror yet" from "VK transport failed" — the caller knows
        the key exists in Jira and is asking us to ensure VK matches.

        Project scope: refuses keys outside `_jira_project_key`. The full
        sweep filters by project at the JQL layer; the targeted path bypasses
        that, so without this check `POST /sync/tick/OTHER-1` on a SCRUM-
        configured server would happily fetch OTHER-1 and create a mirror
        in the SCRUM-tied VK project.
        """
        stats = SyncStats()

        if not jira_key.startswith(self._jira_project_key + "-"):
            logger.warning(
                "tick_for_key refused: key %r is outside configured project %r",
                jira_key, self._jira_project_key,
            )
            stats.skipped_unsupported += 1
            return stats

        try:
            raw = self._jira.get_issue(jira_key)
        except Exception as e:
            logger.error("Jira get_issue failed for %s: %s", jira_key, e)
            stats.errors += 1
            return stats

        try:
            vk_issues = self._mcp.list_all_issues(
                self._vk_project_id, page_size=self._page_size
            )
        except Exception as e:
            logger.error("VK list failed for project %s: %s", self._vk_project_id, e)
            stats.errors += 1
            return stats

        vk_by_key = self._index_vk_by_key(vk_issues)
        snap = JiraIssueSnapshot.from_search_hit(cast(dict[str, JSONValue], raw))
        self._sync_one(snap, vk_by_key, stats)

        # Deliberately do NOT update `_last_vk_bound_count` here. The full
        # sweep uses that as a high-water mark to detect "VK silently
        # returned empty" and skip the create path on the next tick. If a
        # targeted call ran while MCP was misbehaving (returning [] under
        # a transport failure), overwriting the high-water with 0 would
        # defeat the guard and a subsequent `tick()` could mass-recreate
        # mirrors. Leave the mark alone — only `tick()` should move it.

        return stats
