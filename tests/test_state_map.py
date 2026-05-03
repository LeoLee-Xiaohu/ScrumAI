"""Unit tests for sync.state_map — pure mapping logic, no I/O."""

from __future__ import annotations

import pytest

from sync.state_map import (
    JIRA_TO_VK,
    JIRA_TRANSITIONS,
    VK_STATUSES,
    VK_TO_JIRA,
    is_syncable_jira_status,
    jira_status_to_vk,
    jira_transition_id,
    vk_status_to_jira,
)


# ----- structural invariants -----


def test_jira_to_vk_inverse_of_vk_to_jira() -> None:
    """Round-tripping a VK status through both maps lands you back where you started."""
    for vk, jira in VK_TO_JIRA.items():
        assert JIRA_TO_VK[jira] == vk


def test_all_vk_statuses_have_a_jira_target() -> None:
    """Every VK status must be reachable from VK->Jira; otherwise VK changes orphan."""
    assert set(VK_TO_JIRA.keys()) == VK_STATUSES


def test_backlog_is_intentionally_excluded_from_jira_to_vk() -> None:
    """Backlog is the one Jira status with no VK equivalent; we skip those issues."""
    assert "Backlog" not in JIRA_TO_VK


# ----- jira_status_to_vk -----


@pytest.mark.parametrize(
    "jira_status,expected",
    [
        ("To Do", "todo"),
        ("In Progress", "inprogress"),
        ("In Review", "inreview"),
        ("Done", "done"),
        ("Cancelled", "cancelled"),
    ],
)
def test_jira_status_to_vk_known_values(jira_status: str, expected: str) -> None:
    assert jira_status_to_vk(jira_status) == expected


def test_jira_status_to_vk_returns_none_for_backlog() -> None:
    """Backlog has no VK mirror — caller must skip rather than guess."""
    assert jira_status_to_vk("Backlog") is None


def test_jira_status_to_vk_returns_none_for_unknown() -> None:
    assert jira_status_to_vk("Wishful Thinking") is None


def test_jira_status_to_vk_is_case_sensitive() -> None:
    """Jira display names are canonical; we don't normalize here.

    If callers see lowercase from Jira, they need to fix that upstream
    rather than hide the bug behind a tolerant lookup.
    """
    assert jira_status_to_vk("to do") is None
    assert jira_status_to_vk("DONE") is None


# ----- vk_status_to_jira -----


@pytest.mark.parametrize(
    "vk_status,expected",
    [
        ("todo", "To Do"),
        ("inprogress", "In Progress"),
        ("inreview", "In Review"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
    ],
)
def test_vk_status_to_jira_known_values(vk_status: str, expected: str) -> None:
    assert vk_status_to_jira(vk_status) == expected


def test_vk_status_to_jira_normalizes_case() -> None:
    """MCP responses may return mixed case; we normalize to lowercase."""
    assert vk_status_to_jira("InProgress") == "In Progress"
    assert vk_status_to_jira("DONE") == "Done"


def test_vk_status_to_jira_returns_none_for_unknown() -> None:
    assert vk_status_to_jira("nope") is None


# ----- jira_transition_id -----


@pytest.mark.parametrize(
    "target,expected_id",
    [
        ("To Do", "11"),
        ("In Progress", "21"),
        ("In Review", "31"),
        ("Done", "41"),
    ],
)
def test_jira_transition_id_known(target: str, expected_id: str) -> None:
    assert jira_transition_id(target) == expected_id


def test_jira_transition_id_missing_for_cancelled_and_backlog() -> None:
    """SCRUM workflow has no transition to these states (probed 2026-05-02)."""
    assert jira_transition_id("Cancelled") is None
    assert jira_transition_id("Backlog") is None


# ----- is_syncable_jira_status -----


def test_is_syncable_for_mapped_statuses() -> None:
    for s in JIRA_TO_VK:
        assert is_syncable_jira_status(s) is True


def test_is_syncable_false_for_backlog() -> None:
    assert is_syncable_jira_status("Backlog") is False


def test_is_syncable_false_for_unknown() -> None:
    assert is_syncable_jira_status("Random") is False


def test_jira_transitions_keys_are_subset_of_jira_to_vk_targets() -> None:
    """Every transition target must be a status we know how to reach.

    If we add a transition to a status not in JIRA_TO_VK, we'd be able to
    push Jira to a status we then can't sync to VK — broken round-trip.
    """
    transition_targets = set(JIRA_TRANSITIONS.keys())
    sync_targets = set(JIRA_TO_VK.keys())
    assert transition_targets.issubset(sync_targets), (
        f"transition targets not in JIRA_TO_VK: {transition_targets - sync_targets}"
    )
