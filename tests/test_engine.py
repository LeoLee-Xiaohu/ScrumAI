"""Unit tests for sync.engine — MirrorLedger TTL behavior and SyncEngine orchestration."""

from __future__ import annotations

import time
from typing import Callable

import httpx

from jira_client import JiraClient
from mcp_adapter import McpIssue
from sync.engine import MirrorLedger, SyncEngine, TickReport
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


# ----- MirrorLedger -----


def test_ledger_records_and_recalls_vk_push() -> None:
    ledger = MirrorLedger()
    ledger.record_pushed_to_vk("SCRUM-1", "inprogress")

    assert ledger.vk_status_was_pushed("SCRUM-1", "inprogress") is True
    # Different status for same key: not a match.
    assert ledger.vk_status_was_pushed("SCRUM-1", "done") is False
    # Different key: not a match.
    assert ledger.vk_status_was_pushed("SCRUM-2", "inprogress") is False


def test_ledger_records_and_recalls_jira_push() -> None:
    ledger = MirrorLedger()
    ledger.record_pushed_to_jira("SCRUM-1", "In Progress")

    assert ledger.jira_status_was_pushed("SCRUM-1", "In Progress") is True
    assert ledger.jira_status_was_pushed("SCRUM-1", "Done") is False


def test_ledger_normalizes_vk_status_case() -> None:
    """VK statuses are case-insensitive on the wire; the ledger must agree."""
    ledger = MirrorLedger()
    ledger.record_pushed_to_vk("SCRUM-1", "InProgress")
    assert ledger.vk_status_was_pushed("SCRUM-1", "inprogress") is True
    assert ledger.vk_status_was_pushed("SCRUM-1", "INPROGRESS") is True


def test_ledger_entries_expire_after_ttl() -> None:
    ledger = MirrorLedger(ttl_seconds=0.05)
    ledger.record_pushed_to_vk("SCRUM-1", "done")
    assert ledger.vk_status_was_pushed("SCRUM-1", "done") is True

    time.sleep(0.1)
    assert ledger.vk_status_was_pushed("SCRUM-1", "done") is False


def test_ledger_directions_are_independent() -> None:
    """A 'jira' record must not satisfy a 'vk' query and vice versa."""
    ledger = MirrorLedger()
    ledger.record_pushed_to_jira("SCRUM-1", "Done")

    # The lookup expects a VK-side status, so even if "Done" coincidentally
    # matched, it shouldn't trigger the wrong direction.
    assert ledger.vk_status_was_pushed("SCRUM-1", "Done") is False


# ----- SyncEngine.tick orchestration -----


def make_jira_search_handler(issues: list[dict]) -> Callable[[httpx.Request], httpx.Response]:
    """Serve a single SCRUM project search; return 404 for everything else."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": issues})
        if req.url.path.startswith("/rest/api/3/issue/") and req.method == "GET":
            return httpx.Response(
                200,
                json={"key": "X", "fields": {"status": {"name": "To Do"}}},
            )
        return httpx.Response(204)
    return handler


def test_engine_tick_runs_both_directions() -> None:
    """A clean tick reports stats from both syncers without crashing."""
    handler = make_jira_search_handler([])
    mcp = FakeMcpClient()

    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira,
            mcp=mcp,
            jira_project_key="SCRUM",
            vk_project_id="p",
            interval_seconds=0.01,
        )
        report = engine.tick()

    assert isinstance(report, TickReport)
    assert report.jira_to_vk_errors == 0
    assert report.vk_to_jira_errors == 0


def test_engine_tick_creates_then_settles() -> None:
    """End-to-end: Jira issue exists, no VK mirror -> tick 1 creates, tick 2 idle."""
    issues = [
        {
            "key": "SCRUM-1",
            "fields": {
                "summary": "Build it",
                "status": {"name": "To Do"},
                "description": None,
            },
        }
    ]
    handler = make_jira_search_handler(issues)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira,
            mcp=mcp,
            jira_project_key="SCRUM",
            vk_project_id="p",
            interval_seconds=0.01,
        )
        first = engine.tick()
        second = engine.tick()

    assert first.jira_to_vk_created == 1
    assert second.jira_to_vk_created == 0
    assert second.total_writes() == 0


def test_engine_anti_loop_jira_to_vk_then_vk_to_jira_doesnt_bounce() -> None:
    """Jira->VK push, then VK->Jira sees the mirrored VK status; ledger blocks the echo."""
    # Jira issue is in progress; VK is empty initially.
    issues = [
        {
            "key": "SCRUM-1",
            "fields": {
                "summary": "x",
                "status": {"name": "In Progress"},
                "description": None,
            },
        }
    ]

    transition_calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": issues})
        if req.url.path.startswith("/rest/api/3/issue/") and req.method == "GET":
            return httpx.Response(
                200,
                json={"key": "SCRUM-1", "fields": {"status": {"name": "In Progress"}}},
            )
        if req.url.path.endswith("/transitions") and req.method == "POST":
            transition_calls.append(req.url.path)
            return httpx.Response(204)
        return httpx.Response(404)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira,
            mcp=mcp,
            jira_project_key="SCRUM",
            vk_project_id="p",
            interval_seconds=0.01,
        )
        report = engine.tick()

    # Jira->VK created VK task and set status to inprogress.
    assert report.jira_to_vk_created == 1
    # VK->Jira saw VK="inprogress" but Jira is already "In Progress" — same-state
    # check + ledger both prevent a bounce.
    assert report.vk_to_jira_transitioned == 0
    assert transition_calls == []


def test_engine_anti_loop_vk_to_jira_then_jira_to_vk_doesnt_bounce() -> None:
    """Real-world flow: sync establishes baseline, VK status moves, change flows to Jira.

    Cold-start contract: when sync starts and Jira/VK already disagree, Jira
    wins on tick 1 (reset VK to canonical Jira state). After that baseline
    is established, *changes* on either side flow correctly. This test
    models the realistic operator flow:

      tick 1: baseline; Jira="To Do", VK gets reset to "todo".
      operator moves VK to "done" (execution finishes).
      tick 2: VK->Jira detects the change, transitions Jira to "Done".
      tick 3: Jira reports "Done"; ledger + same-state check prevent bounce
              back to VK.
    """
    jira_status_holder = {"value": "To Do"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "SCRUM-1",
                            "fields": {
                                "summary": "x",
                                "status": {"name": jira_status_holder["value"]},
                                "description": None,
                            },
                        }
                    ]
                },
            )
        if req.url.path.startswith("/rest/api/3/issue/SCRUM-1") and req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "key": "SCRUM-1",
                    "fields": {"status": {"name": jira_status_holder["value"]}},
                },
            )
        if req.url.path.endswith("/transitions") and req.method == "POST":
            import json as _json
            body = _json.loads(req.read())
            tid = body["transition"]["id"]
            if tid == "41":
                jira_status_holder["value"] = "Done"
            return httpx.Response(204)
        return httpx.Response(404)

    # Tick 1 starting state: VK and Jira already agree on "todo"/"To Do".
    # This models the steady state after a previous sync run.
    mcp = FakeMcpClient(
        issues=[
            McpIssue(id="vk-1", simple_id="1", title="[SCRUM-1] x", status="todo")
        ]
    )
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira,
            mcp=mcp,
            jira_project_key="SCRUM",
            vk_project_id="p",
            interval_seconds=0.01,
        )
        # Tick 1: baseline observation. Both sides agree -> no writes.
        baseline = engine.tick()
        assert baseline.total_writes() == 0

        # Operator moves VK to "done" between ticks (simulating execution).
        mcp.issues[0].status = "done"

        # Tick 2: VK->Jira detects the move, transitions Jira to Done.
        push = engine.tick()
        assert push.vk_to_jira_transitioned == 1
        assert jira_status_holder["value"] == "Done"

        # Tick 3: Jira now reports Done. Must NOT bounce back to VK
        # (VK is already done; ledger entry from tick 2 also blocks).
        echo = engine.tick()
        assert echo.jira_to_vk_updated == 0
        assert echo.vk_to_jira_transitioned == 0
        assert echo.total_writes() == 0


# ----- adaptive polling -----


def test_next_interval_seconds_cold_when_no_writes() -> None:
    """Fresh engine with no writes -> cold interval.

    Cold-start treats "never written" as cold so we don't spam APIs in a
    quiet project. Hot-cadence kicks in only after observed activity.
    """
    handler = make_jira_search_handler([])
    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira, mcp=mcp,
            jira_project_key="SCRUM", vk_project_id="p",
            hot_interval_seconds=30.0,
            cold_interval_seconds=300.0,
            hot_window_seconds=3600.0,
        )

    assert engine.last_write_at is None
    assert engine.next_interval_seconds() == 300.0


def test_next_interval_seconds_hot_after_recent_write() -> None:
    """A successful write flips the next interval to hot."""
    issues = [
        {
            "key": "SCRUM-1",
            "fields": {
                "summary": "x", "status": {"name": "To Do"}, "description": None,
            },
        }
    ]
    handler = make_jira_search_handler(issues)
    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira, mcp=mcp,
            jira_project_key="SCRUM", vk_project_id="p",
            hot_interval_seconds=30.0,
            cold_interval_seconds=300.0,
            hot_window_seconds=3600.0,
        )
        report = engine.tick()

    assert report.jira_to_vk_created == 1
    assert engine.last_write_at is not None
    assert engine.next_interval_seconds() == 30.0


def test_next_interval_seconds_cold_after_window_expired() -> None:
    """Past hot_window_seconds since last write -> cold again.

    Simulated by hand-setting `_last_write_at` to a past monotonic value.
    Beats the brittleness of sleeping in the test.
    """
    handler = make_jira_search_handler([])
    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira, mcp=mcp,
            jira_project_key="SCRUM", vk_project_id="p",
            hot_interval_seconds=30.0,
            cold_interval_seconds=300.0,
            hot_window_seconds=3600.0,
        )
        # 2 hours ago — well past the 1-hour hot window.
        engine._last_write_at = time.monotonic() - 7200.0

    assert engine.next_interval_seconds() == 300.0


def test_idle_tick_does_not_advance_last_write_at() -> None:
    """Tick with zero writes leaves the timestamp untouched.

    Otherwise repeated empty ticks would keep the loop hot forever.
    """
    handler = make_jira_search_handler([])
    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira, mcp=mcp,
            jira_project_key="SCRUM", vk_project_id="p",
            hot_interval_seconds=30.0,
            cold_interval_seconds=300.0,
        )
        report = engine.tick()

    assert report.total_writes() == 0
    assert engine.last_write_at is None


# ----- tick_for_key orchestration -----


def test_tick_for_key_routes_to_targeted_syncers() -> None:
    """tick_for_key calls jira.get_issue (not /search/jql) for the targeted key."""
    get_issue_calls: list[str] = []
    search_calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            search_calls.append(req.url.path)
            return httpx.Response(200, json={"issues": []})
        if req.url.path.startswith("/rest/api/3/issue/") and req.method == "GET":
            tail = req.url.path[len("/rest/api/3/issue/"):]
            if "/" not in tail:
                get_issue_calls.append(tail)
                return httpx.Response(
                    200,
                    json={
                        "key": tail,
                        "fields": {
                            "summary": "x",
                            "status": {"name": "To Do"},
                            "description": None,
                        },
                    },
                )
        return httpx.Response(404)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira, mcp=mcp,
            jira_project_key="SCRUM", vk_project_id="p",
            interval_seconds=0.01,
        )
        report = engine.tick_for_key("SCRUM-500")

    # Jira->VK side fetched directly (no search), then VK->Jira saw the
    # newly created VK task and skipped (Jira already in 'To Do').
    assert get_issue_calls == ["SCRUM-500"]
    assert search_calls == []
    assert report.jira_to_vk_created == 1


def test_tick_for_key_updates_last_write_at_on_create() -> None:
    """A successful targeted create must flip the engine to hot mode too."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/issue/SCRUM-501" and req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "key": "SCRUM-501",
                    "fields": {
                        "summary": "x",
                        "status": {"name": "To Do"},
                        "description": None,
                    },
                },
            )
        return httpx.Response(404)

    mcp = FakeMcpClient()
    with make_jira_client(handler) as jira:
        engine = SyncEngine(
            jira=jira, mcp=mcp,
            jira_project_key="SCRUM", vk_project_id="p",
            hot_interval_seconds=30.0,
            cold_interval_seconds=300.0,
        )
        assert engine.last_write_at is None
        report = engine.tick_for_key("SCRUM-501")

    assert report.jira_to_vk_created == 1
    assert engine.last_write_at is not None
    assert engine.next_interval_seconds() == 30.0
