"""Unit tests for sync.vk_to_jira."""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from jira_client import JiraClient
from mcp_adapter import McpIssue
from sync.engine import MirrorLedger
from sync.vk_to_jira import VkToJiraSyncer
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


class JiraRecorder:
    """Capture transition POSTs and serve canned get_issue responses."""

    def __init__(self, status_by_key: dict[str, str]) -> None:
        self.status_by_key = status_by_key
        self.transitions: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # GET /rest/api/3/issue/{key}
        if path.startswith("/rest/api/3/issue/") and request.method == "GET":
            # Path is /rest/api/3/issue/<key> (no trailing /transitions)
            tail = path[len("/rest/api/3/issue/"):]
            if "/" not in tail:
                key = tail
                return httpx.Response(
                    200,
                    json={
                        "key": key,
                        "fields": {
                            "status": {"name": self.status_by_key.get(key, "To Do")},
                        },
                    },
                )
        # POST /rest/api/3/issue/{key}/transitions
        if path.endswith("/transitions") and request.method == "POST":
            tail = path[len("/rest/api/3/issue/"):]
            key = tail.split("/", 1)[0]
            payload = request.read()
            import json as _json
            body = _json.loads(payload) if payload else {}
            transition_id = body.get("transition", {}).get("id", "")
            self.transitions.append((key, transition_id))
            return httpx.Response(204)
        return httpx.Response(404, text=f"unexpected: {request.method} {path}")


# ----- happy paths -----


def test_tick_transitions_jira_when_vk_status_advances() -> None:
    """VK moves to 'inprogress' while Jira is still 'To Do' -> push transition."""
    recorder = JiraRecorder(status_by_key={"SCRUM-1": "To Do"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-1",
                simple_id="1",
                title="[SCRUM-1] x",
                status="inprogress",
            )
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick()

    assert stats.transitioned == 1
    # 21 is the In Progress transition id from state_map.
    assert recorder.transitions == [("SCRUM-1", "21")]


def test_tick_no_transition_when_already_in_sync() -> None:
    """VK and Jira already match -> no transition call."""
    recorder = JiraRecorder(status_by_key={"SCRUM-2": "Done"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-2",
                simple_id="2",
                title="[SCRUM-2] x",
                status="done",
            )
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick()

    assert stats.transitioned == 0
    assert stats.skipped_unchanged == 1
    assert recorder.transitions == []


def test_tick_skips_orphan_vk_tasks() -> None:
    """VK tasks without `[KEY-NN]` prefix are local-only; ignore them."""
    recorder = JiraRecorder(status_by_key={})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(id="vk-orphan", simple_id="o", title="locally created", status="done")
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick()

    assert stats.skipped_orphan == 1
    assert stats.transitioned == 0
    assert recorder.transitions == []


def test_tick_idempotent_after_successful_transition() -> None:
    """After we push, the cache should keep us from re-fetching/transitioning."""
    recorder = JiraRecorder(status_by_key={"SCRUM-3": "To Do"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-3", simple_id="3", title="[SCRUM-3] x", status="inprogress"
            )
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        # First tick transitions
        syncer.tick()
        # Simulate the next poll where Jira hasn't changed back but VK status is the same.
        # The cache should short-circuit before any HTTP call.
        before = len(recorder.transitions)
        stats = syncer.tick()

    assert stats.skipped_unchanged == 1
    assert len(recorder.transitions) == before  # no new transition issued


def test_tick_handles_unmappable_vk_status() -> None:
    """A VK status outside our state map (e.g. future addition) is skipped, not crashed."""
    recorder = JiraRecorder(status_by_key={"SCRUM-4": "To Do"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-4",
                simple_id="4",
                title="[SCRUM-4] x",
                status="weirdunknown",
            )
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick()

    assert stats.skipped_unsupported == 1
    assert stats.transitioned == 0


def test_tick_handles_no_workflow_transition() -> None:
    """VK 'cancelled' has no SCRUM workflow transition -> warn-and-skip, not error."""
    recorder = JiraRecorder(status_by_key={"SCRUM-5": "To Do"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-5", simple_id="5", title="[SCRUM-5] x", status="cancelled"
            )
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick()

    assert stats.skipped_unsupported == 1
    assert stats.transitioned == 0
    assert recorder.transitions == []


# ----- error paths -----


def test_tick_records_error_on_jira_get_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(503, text="upstream down")
        return httpx.Response(404)

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-6", simple_id="6", title="[SCRUM-6] x", status="inprogress"
            )
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick()

    assert stats.errors >= 1
    assert stats.transitioned == 0


def test_tick_does_not_cache_vk_status_on_transition_failure() -> None:
    """A failed `transition_issue` must not poison `_last_seen_vk_status`.

    Symmetric to the Jira->VK fix in jira_to_vk: if we cache after a failed
    write, the fast-path skip on the next tick (`previous == vk_status_lc`)
    silently swallows the drift forever. The current implementation skips
    cache update on the exception path; this test pins that contract so a
    refactor can't silently regress it.
    """
    transition_attempts: list[int] = []
    recorder_status = {"SCRUM-90": "To Do"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/rest/api/3/issue/") and request.method == "GET":
            tail = path[len("/rest/api/3/issue/"):]
            if "/" not in tail:
                return httpx.Response(
                    200,
                    json={
                        "key": tail,
                        "fields": {
                            "status": {"name": recorder_status.get(tail, "To Do")},
                        },
                    },
                )
        if path.endswith("/transitions") and request.method == "POST":
            transition_attempts.append(1)
            # Fail the first attempt, succeed the second.
            if len(transition_attempts) == 1:
                return httpx.Response(500, text="boom")
            return httpx.Response(204)
        return httpx.Response(404, text=f"unexpected: {request.method} {path}")

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-90", simple_id="90", title="[SCRUM-90] x", status="inprogress"
            )
        ]
    )
    with make_jira_client(handler) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")

        # Tick 1: transition POST fails — error counted, cache untouched.
        first = syncer.tick()
        assert first.errors == 1
        assert first.transitioned == 0
        assert len(transition_attempts) == 1

        # Tick 2: VK still 'inprogress', Jira still 'To Do'. Without the
        # cache-discipline fix the syncer would skip via the
        # `previous == vk_status_lc` fast path. With it, we retry.
        second = syncer.tick()

    assert second.errors == 0
    assert second.transitioned == 1
    assert len(transition_attempts) == 2


# ----- ledger anti-loop -----


def test_tick_skips_when_ledger_says_vk_status_was_just_pushed() -> None:
    """If Jira->VK just pushed VK to 'inprogress', don't bounce it back to Jira."""
    recorder = JiraRecorder(status_by_key={"SCRUM-7": "To Do"})

    ledger = MirrorLedger(ttl_seconds=60.0)
    ledger.record_pushed_to_vk("SCRUM-7", "inprogress")

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-7", simple_id="7", title="[SCRUM-7] x", status="inprogress"
            )
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(
            jira=jira, mcp=mcp, vk_project_id="p", ledger=ledger
        )
        stats = syncer.tick()

    assert stats.skipped_unchanged == 1
    assert recorder.transitions == []


def test_tick_records_to_ledger_on_transition() -> None:
    recorder = JiraRecorder(status_by_key={"SCRUM-8": "To Do"})

    ledger = MirrorLedger()
    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-8", simple_id="8", title="[SCRUM-8] x", status="inprogress"
            )
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(
            jira=jira, mcp=mcp, vk_project_id="p", ledger=ledger
        )
        syncer.tick()

    assert ledger.jira_status_was_pushed("SCRUM-8", "In Progress")


# ----- tick_for_key — targeted single-task sync -----


def test_tick_for_key_transitions_only_matching_vk_task() -> None:
    """Multiple VK tasks present; only the one matching the key is touched."""
    recorder = JiraRecorder(status_by_key={"SCRUM-200": "To Do", "SCRUM-201": "Done"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-200", simple_id="200",
                title="[SCRUM-200] target", status="inprogress",
            ),
            McpIssue(
                id="vk-201", simple_id="201",
                title="[SCRUM-201] sibling", status="inprogress",
            ),
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick_for_key("SCRUM-200")

    assert stats.transitioned == 1
    # Only SCRUM-200 was transitioned; SCRUM-201 was untouched even though
    # its VK status also disagrees with Jira ("Done" vs "inprogress").
    assert recorder.transitions == [("SCRUM-200", "21")]


def test_tick_for_key_no_op_when_no_matching_vk_task() -> None:
    """If no VK task carries the key, transitioned=0 and no Jira call.

    This is the legitimate case where Jira->VK hasn't created a mirror yet
    (e.g., new ticket, polling hasn't fired). The targeted endpoint must
    not error out — Jira->VK side handles creation.
    """
    recorder = JiraRecorder(status_by_key={"SCRUM-300": "To Do"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-other", simple_id="o",
                title="[SCRUM-1] unrelated", status="todo",
            ),
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick_for_key("SCRUM-300")

    assert stats.transitioned == 0
    assert stats.skipped_orphan == 0  # filter happens before counting
    assert recorder.transitions == []


def test_tick_for_key_records_error_on_vk_list_failure() -> None:
    """If MCP list_all_issues raises, error is recorded, no Jira call."""
    class _BoomMcp(FakeMcpClient):
        def list_all_issues(self, *a, **kw):  # type: ignore[override]
            raise RuntimeError("mcp transport down")

    recorder = JiraRecorder(status_by_key={})
    mcp = _BoomMcp()
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(jira=jira, mcp=mcp, vk_project_id="p")
        stats = syncer.tick_for_key("SCRUM-400")

    assert stats.errors == 1
    assert recorder.transitions == []


def test_tick_for_key_refuses_keys_outside_configured_project() -> None:
    """Symmetric to JiraToVkSyncer.tick_for_key: if a jira_project_key is
    configured, a key from a different project must NOT touch Jira or VK.

    Without this, a misrouted `POST /sync/tick/OTHER-1` against a SCRUM-tied
    server could still transition a wrong-project Jira issue when a stray
    `[OTHER-1] ...` task happens to exist in VK.
    """
    recorder = JiraRecorder(status_by_key={"OTHER-1": "To Do"})

    mcp = FakeMcpClient(
        issues=[
            McpIssue(
                id="vk-other", simple_id="o",
                title="[OTHER-1] stray", status="inprogress",
            ),
        ]
    )
    with make_jira_client(recorder) as jira:
        syncer = VkToJiraSyncer(
            jira=jira, mcp=mcp, vk_project_id="p", jira_project_key="SCRUM"
        )
        stats = syncer.tick_for_key("OTHER-1")

    assert stats.skipped_unsupported == 1
    assert stats.transitioned == 0
    assert recorder.transitions == []  # no Jira write attempted at all
