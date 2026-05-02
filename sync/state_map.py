"""Bidirectional status mapping between Jira and Vibe Kanban.

Truth source for VK enum: vibe-kanban repo `crates/db/src/models/task.rs`,
the `TaskStatus` enum (lowercase serialization). The forge type definition in
`scrumai-forge/src/types/index.ts` (`pending|in_progress|completed|cancelled`)
is outdated — do NOT use it as a reference.

This module is pure: no I/O, no global state. It exists so jira_to_vk.py and
vk_to_jira.py share one source of truth for the mapping.
"""

from __future__ import annotations

from typing import Final

# VK TaskStatus enum (lowercase wire format, case-insensitive on input).
VK_TODO: Final = "todo"
VK_INPROGRESS: Final = "inprogress"
VK_INREVIEW: Final = "inreview"
VK_DONE: Final = "done"
VK_CANCELLED: Final = "cancelled"

VK_STATUSES: Final = frozenset(
    {VK_TODO, VK_INPROGRESS, VK_INREVIEW, VK_DONE, VK_CANCELLED}
)

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

    Input is normalized to lowercase to tolerate case variation from MCP responses.
    """
    return VK_TO_JIRA.get(vk_status.lower())


def jira_transition_id(target_status: str) -> str | None:
    """Look up the Jira transition id needed to move an issue *to* `target_status`.

    Returns None for statuses with no configured transition (e.g. Backlog or
    Cancelled in the current SCRUM workflow).
    """
    return JIRA_TRANSITIONS.get(target_status)


def is_syncable_jira_status(jira_status: str) -> bool:
    """True iff this Jira status maps to a VK status (i.e. should be synced)."""
    return jira_status in JIRA_TO_VK
