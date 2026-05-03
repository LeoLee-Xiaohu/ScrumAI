"""Bidirectional status mapping between Jira and Vibe Kanban.

VK has two status surfaces:
- The Rust enum in `crates/db/src/models/task.rs` (TaskStatus, lowercase).
- The MCP/JSON-RPC wire format on vibe-kanban 0.1.43, which is sentence-case
  with spaces: `'To do'`, `'In progress'`, `'In review'`, `'Done'`,
  `'Cancelled'`. `update_issue` *silently ignores* lowercase variants like
  `'inprogress'` — it returns success but the status doesn't change.
  Reads always come back in display form.

This module keeps a compact internal canonical form (`todo`, `inprogress`,
...) for routing logic and exposes:
- `vk_status_to_display(canonical)` for outbound writes,
- `normalize_vk_status(raw)` for inbound reads,
so the syncers can stay unaware of the wire format quirk.

The forge type definition in `scrumai-forge/src/types/index.ts`
(`pending|in_progress|completed|cancelled`) is outdated — don't use it.
"""

from __future__ import annotations

from typing import Final

# Canonical internal form (compact, lowercase). Used for routing and ledger keys.
VK_TODO: Final = "todo"
VK_INPROGRESS: Final = "inprogress"
VK_INREVIEW: Final = "inreview"
VK_DONE: Final = "done"
VK_CANCELLED: Final = "cancelled"

VK_STATUSES: Final = frozenset(
    {VK_TODO, VK_INPROGRESS, VK_INREVIEW, VK_DONE, VK_CANCELLED}
)

# Display form expected on the wire by vibe-kanban 0.1.43. `update_issue` is
# case-tolerant on first letter ('In Progress' → 'In progress') but does NOT
# accept the lowercase compact form ('inprogress' silently no-ops).
VK_DISPLAY: Final[dict[str, str]] = {
    VK_TODO: "To do",
    VK_INPROGRESS: "In progress",
    VK_INREVIEW: "In review",
    VK_DONE: "Done",
    VK_CANCELLED: "Cancelled",
}

# Jira status display names as observed in project SCRUM (2026-05-02 probe).
JIRA_TODO: Final = "To Do"
JIRA_INPROGRESS: Final = "In Progress"
JIRA_INREVIEW: Final = "In Review"
JIRA_DONE: Final = "Done"
JIRA_CANCELLED: Final = "Cancelled"
JIRA_BACKLOG: Final = "Backlog"

# Jira transition IDs in project SCRUM (2026-05-02 probe — fixed across all source statuses).
JIRA_TRANSITIONS: Final[dict[str, str]] = {
    JIRA_TODO: "11",
    JIRA_INPROGRESS: "21",
    JIRA_INREVIEW: "31",
    JIRA_DONE: "41",
}


# Jira status name -> VK status. Backlog has no VK equivalent and is intentionally
# omitted: a Backlog issue should not be synced to VK at all.
JIRA_TO_VK: Final[dict[str, str]] = {
    JIRA_TODO: VK_TODO,
    JIRA_INPROGRESS: VK_INPROGRESS,
    JIRA_INREVIEW: VK_INREVIEW,
    JIRA_DONE: VK_DONE,
    JIRA_CANCELLED: VK_CANCELLED,
}

# VK status -> Jira target status name. Round-trips losslessly for all 5 VK statuses.
VK_TO_JIRA: Final[dict[str, str]] = {
    VK_TODO: JIRA_TODO,
    VK_INPROGRESS: JIRA_INPROGRESS,
    VK_INREVIEW: JIRA_INREVIEW,
    VK_DONE: JIRA_DONE,
    VK_CANCELLED: JIRA_CANCELLED,
}


def jira_status_to_vk(jira_status: str) -> str | None:
    """Map a Jira status display name to the VK enum value.

    Returns None for statuses with no VK equivalent (e.g. Backlog), signalling
    the caller to skip the issue rather than guess.
    """
    return JIRA_TO_VK.get(jira_status)


def vk_status_to_jira(vk_status: str) -> str | None:
    """Map a VK status to the Jira status display name.

    Input is normalized via `normalize_vk_status` to tolerate any wire form
    (display 'To do', compact 'todo', or camel 'InProgress').
    """
    return VK_TO_JIRA.get(normalize_vk_status(vk_status))


def normalize_vk_status(raw: str) -> str:
    """Collapse any VK wire-format status to the canonical compact form.

    Tolerates: 'To do', 'todo', 'TODO', 'In Progress', 'in_progress', 'InProgress'.
    Returns the input lowercased with spaces and underscores stripped.
    Unknown values pass through (lowercased) so callers can detect them.
    """
    return (raw or "").replace(" ", "").replace("_", "").lower()


def vk_status_to_display(canonical: str) -> str | None:
    """Translate a canonical VK status to the wire display form for `update_issue`.

    Required because vibe-kanban 0.1.43 silently ignores lowercase compact
    forms — `update_issue(status='inprogress')` returns success but doesn't
    apply. Display form ('In progress') is what actually sticks.
    """
    return VK_DISPLAY.get(canonical)


def jira_transition_id(target_status: str) -> str | None:
    """Look up the Jira transition id needed to move an issue *to* `target_status`.

    Returns None for statuses with no configured transition (e.g. Backlog or
    Cancelled in the current SCRUM workflow).
    """
    return JIRA_TRANSITIONS.get(target_status)


def is_syncable_jira_status(jira_status: str) -> bool:
    """True iff this Jira status maps to a VK status (i.e. should be synced)."""
    return jira_status in JIRA_TO_VK
