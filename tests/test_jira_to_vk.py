"""Unit tests for sync.jira_to_vk."""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from jira_client import JiraClient
from mcp_adapter import McpIssue
from sync.engine import MirrorLedger
from sync.jira_to_vk import (
    JiraIssueSnapshot,
    JiraToVkSyncer,
    _adf_to_plain,
    make_vk_title,
    parse_jira_key_from_title,
)
from tests.sync_fakes import FakeMcpClient


BASE_URL = "https://example.atlassian.net"


def make_jira_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> JiraClient:
    return JiraClient(
        base_url=BASE_URL,
        email="t@example.com",
        api_token="tok",
        transport=httpx.MockTransport(handler),
    )


def make_search_response(issues: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"issues": issues})


def jira_issue(
    key: str,
    summary: str,
    status_name: str,
    description: str | None = None,
) -> dict:
    desc = None
    if description is not None:
        desc = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status_name},
            "description": desc,
        },
    }


# ----- title parsing -----


@pytest.mark.parametrize(
    "title,expected_key,expected_rest",
    [
        ("[SCRUM-27] Build login", "SCRUM-27", "Build login"),
        ("[PROJ-1] x", "PROJ-1", "x"),
        ("[ABC_DEF-12] foo bar", "ABC_DEF-12", "foo bar"),
    ],
)
def test_parse_jira_key_from_title_extracts(
    title: str, expected_key: str, expected_rest: str
) -> None:
    key, rest = parse_jira_key_from_title(title)
    assert key == expected_key
    assert rest == expected_rest


@pytest.mark.parametrize(
    "title",
    [
        "no prefix",
        "[lowercase-1] x",  # project keys are uppercase
        "[NO-NUMBER] x",
        "[]",
        "",
    ],
)
def test_parse_jira_key_from_title_no_match(title: str) -> None:
    key, rest = parse_jira_key_from_title(title)
    assert key is None
    assert rest == title


def test_make_vk_title_round_trips_via_parser() -> None:
    title = make_vk_title("SCRUM-99", "Original summary")
    key, rest = parse_jira_key_from_title(title)
    assert key == "SCRUM-99"
    assert rest == "Original summary"


# ----- ADF flattening -----


def test_adf_to_plain_simple_paragraph() -> None:
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello world"}]}
        ],
    }
    assert _adf_to_plain(doc) == "Hello world"


def test_adf_to_plain_nested_blocks() -> None:
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "First"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]},
        ],
    }
    out = _adf_to_plain(doc)
    assert "First" in out
    assert "Second" in out
    # Block-level nodes get newline separators so paragraphs don't run together.
    assert "\n" in out


def test_adf_to_plain_handles_none() -> None:
    assert _adf_to_plain(None) == ""


def test_adf_to_plain_handles_unexpected_shape() -> None:
    # Garbage input shouldn't raise — the syncer treats missing description as ""
    assert _adf_to_plain({"weird": "thing"}) == ""


# ----- JiraIssueSnapshot -----


def test_snapshot_extracts_fields() -> None:
    raw = jira_issue("SCRUM-1", "Summary", "To Do", "Body")
    snap = JiraIssueSnapshot.from_search_hit(raw)
    assert snap.key == "SCRUM-1"
    assert snap.summary == "Summary"
    assert snap.status_name == "To Do"
    assert "Body" in snap.description_text


def test_snapshot_handles_missing_fields() -> None:
    snap = JiraIssueSnapshot.from_search_hit({"key": "X-1"})
    assert snap.key == "X-1"
    assert snap.summary == ""
    assert snap.status_name == ""
    assert snap.description_text == ""


# ----- syncer.tick — happy paths -----


def test_tick_creates_vk_for_new_jira_issue() -> None:
    issues = [jira_issue("SCRUM-1", "Login feature", "To Do", "Build it")]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return make_search_response(issues)
        return httpx.Response(404)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="proj-1"
        )
        stats = syncer.tick()

    assert stats.created == 1
    assert len(mcp.created_calls) == 1
    assert mcp.created_calls[0]["title"] == "[SCRUM-1] Login feature"
    assert mcp.created_calls[0]["description"] == "Build it"
    # Status was already "todo" (default), no follow-up update needed.
    assert mcp.updated_calls == []


def test_tick_creates_then_transitions_for_non_todo_jira_status() -> None:
    """A Jira issue already 'In Progress' should land on VK 'inprogress'."""
    issues = [jira_issue("SCRUM-2", "WIP", "In Progress")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        syncer.tick()

    assert len(mcp.created_calls) == 1
    # Then a follow-up update_issue to set status. VK 0.1.43 wire format
    # is sentence case ('In progress') — see sync.state_map.VK_DISPLAY.
    assert len(mcp.updated_calls) == 1
    assert mcp.updated_calls[0]["status"] == "In progress"


def test_tick_skips_backlog_issues() -> None:
    issues = [jira_issue("SCRUM-3", "Idea", "Backlog")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats = syncer.tick()

    assert stats.created == 0
    assert stats.skipped_unsupported == 1
    assert mcp.created_calls == []


def test_tick_updates_existing_when_jira_status_changes() -> None:
    issues = [jira_issue("SCRUM-4", "Done thing", "Done")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-1",
                simple_id="1",
                title="[SCRUM-4] Done thing",
                status="inprogress",
            )
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats = syncer.tick()

    assert stats.updated_status == 1
    assert mcp.created_calls == []
    # Display wire form, not 'done' — see sync.state_map.VK_DISPLAY.
    assert any(c["status"] == "Done" for c in mcp.updated_calls)


def test_tick_updates_title_when_summary_changes() -> None:
    issues = [jira_issue("SCRUM-5", "New summary", "To Do")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-5",
                simple_id="5",
                title="[SCRUM-5] Old summary",
                status="todo",
            )
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats = syncer.tick()

    assert stats.updated_content == 1
    assert mcp.updated_calls[0]["title"] == "[SCRUM-5] New summary"


def test_tick_skips_when_in_sync() -> None:
    """No drift -> no PATCH. Idempotent ticks must not churn VK."""
    issues = [jira_issue("SCRUM-6", "Stable", "In Progress")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-6",
                simple_id="6",
                title="[SCRUM-6] Stable",
                status="inprogress",
            )
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats = syncer.tick()

    assert stats.created == 0
    assert stats.updated_status == 0
    assert stats.updated_content == 0
    assert stats.skipped_unchanged == 1
    assert mcp.updated_calls == []


def test_tick_idempotent_across_two_calls() -> None:
    issues = [jira_issue("SCRUM-7", "X", "To Do")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats1 = syncer.tick()
        stats2 = syncer.tick()

    # First tick creates, second tick is a no-op.
    assert stats1.created == 1
    assert stats2.created == 0
    assert stats2.skipped_unchanged == 1
    assert len(mcp.created_calls) == 1


# ----- syncer.tick — error and edge cases -----


def test_tick_records_error_on_jira_failure() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats = syncer.tick()

    assert stats.errors >= 1
    assert mcp.created_calls == []


def test_tick_records_error_when_create_fails() -> None:
    issues = [jira_issue("SCRUM-8", "x", "To Do")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient(create_should_fail=True)
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats = syncer.tick()

    assert stats.errors == 1
    assert stats.created == 0


def test_tick_ignores_vk_orphans_with_non_jira_titles() -> None:
    """VK tasks not matching `[KEY-NN] ...` are local-only and untouched."""
    issues = [jira_issue("SCRUM-9", "Mine", "To Do")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient(
        issues=[
            McpIssue(id="vk-orphan", simple_id="o", title="local task", status="todo")
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        syncer.tick()

    # The orphan was not updated, but a new VK task was created for SCRUM-9.
    assert any(c["title"] == "[SCRUM-9] Mine" for c in mcp.created_calls)
    assert all(u["issue_id"] != "vk-orphan" for u in mcp.updated_calls)


# ----- ledger anti-loop -----


def test_tick_skips_when_ledger_says_jira_status_was_just_pushed() -> None:
    """If VK->Jira just transitioned Jira to 'In Progress', we don't echo it back."""
    issues = [jira_issue("SCRUM-10", "x", "In Progress")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    ledger = MirrorLedger(ttl_seconds=60.0)
    ledger.record_pushed_to_jira("SCRUM-10", "In Progress")

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-10",
                simple_id="10",
                title="[SCRUM-10] x",
                status="inprogress",
            )
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p",
            ledger=ledger,
        )
        stats = syncer.tick()

    assert stats.skipped_unchanged == 1
    assert mcp.updated_calls == []


def test_tick_records_to_ledger_on_create() -> None:
    issues = [jira_issue("SCRUM-11", "x", "In Progress")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    ledger = MirrorLedger()
    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p",
            ledger=ledger,
        )
        syncer.tick()

    assert ledger.vk_status_was_pushed("SCRUM-11", "inprogress")


# ----- transient VK-empty guard -----


def test_tick_aborts_when_vk_silently_returns_empty_after_seeing_tasks() -> None:
    """VK MCP returns `{issues: []}` when its backend is unreachable.

    Regression for live-smoke incident where SSH tunnel went down between
    ticks, MCP returned empty list, and the engine tried to recreate every
    Jira issue. With history of N>0 bound tasks, an empty tick must abort
    without firing creates.
    """
    issues = [
        jira_issue("SCRUM-12", "x", "To Do"),
        jira_issue("SCRUM-13", "y", "In Progress"),
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient(
        issues=[
            McpIssue(id="vk-12", simple_id="12", title="[SCRUM-12] x", status="todo"),
            McpIssue(id="vk-13", simple_id="13", title="[SCRUM-13] y", status="inprogress"),
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        # First tick observes both tasks normally.
        first = syncer.tick()
        assert first.errors == 0

        # Simulate VK MCP returning empty (backend unreachable).
        mcp.issues = []
        second = syncer.tick()

    assert second.errors == 1
    assert mcp.created_calls == []  # no mass-recreate


def test_tick_allows_empty_vk_on_cold_start() -> None:
    """First tick against a truly empty VK should still create mirrors."""
    issues = [jira_issue("SCRUM-14", "x", "To Do")]

    def handler(req: httpx.Request) -> httpx.Response:
        return make_search_response(issues)

    mcp = FakeMcpClient()  # empty from the start
    with make_jira_client(handler) as jira:
        syncer = JiraToVkSyncer(
            jira=jira, mcp=mcp, jira_project_key="SCRUM", vk_project_id="p"
        )
        stats = syncer.tick()

    assert stats.errors == 0
    assert stats.created == 1
