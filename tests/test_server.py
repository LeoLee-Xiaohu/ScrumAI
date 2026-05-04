"""Unit tests for sync.server — FastAPI endpoints + auth + lock semantics.

Uses FastAPI's TestClient (httpx-backed in-process) to drive the app
without binding a real port. Background polling is disabled in tests so
assertions don't race a concurrent tick — the lifecycle still runs, just
without the auto-poll task.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from jira_client import JiraClient
from sync.engine import SyncEngine
from sync.server import _report_to_dict, create_app
from sync.engine import TickReport
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


def make_engine(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    mcp: FakeMcpClient | None = None,
) -> tuple[SyncEngine, JiraClient]:
    jira = make_jira_client(handler)
    engine = SyncEngine(
        jira=jira,
        mcp=mcp or FakeMcpClient(),
        jira_project_key="SCRUM",
        vk_project_id="p",
        hot_interval_seconds=30.0,
        cold_interval_seconds=300.0,
    )
    return engine, jira


# ----- /health -----


def test_health_returns_ok_without_auth() -> None:
    """Health endpoint must work even when an api_key is configured —
    reverse proxies/uptime monitors don't carry credentials.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="secret", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ----- /sync/tick auth -----


def test_tick_requires_api_key_when_configured() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="s3cret", enable_poll_task=False)

    with jira, TestClient(app) as client:
        # No header at all.
        bad = client.post("/sync/tick")
        assert bad.status_code == 401
        # Wrong header.
        also_bad = client.post("/sync/tick", headers={"X-API-Key": "wrong"})
        assert also_bad.status_code == 401


def test_tick_skips_auth_when_api_key_unset() -> None:
    """Local dev / tests with empty api_key bypass auth entirely."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": []})
        return httpx.Response(404)

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.post("/sync/tick")

    assert resp.status_code == 200


# ----- /sync/tick happy path -----


def test_tick_runs_engine_and_returns_report_json() -> None:
    """POST /sync/tick triggers a real engine.tick() and returns its TickReport."""
    issues = [
        {
            "key": "SCRUM-700",
            "fields": {
                "summary": "x",
                "status": {"name": "To Do"},
                "description": None,
            },
        }
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": issues})
        if req.url.path.startswith("/rest/api/3/issue/") and req.method == "GET":
            return httpx.Response(
                200,
                json={"key": "SCRUM-700", "fields": {"status": {"name": "To Do"}}},
            )
        return httpx.Response(404)

    mcp = FakeMcpClient()
    engine, jira = make_engine(handler, mcp=mcp)
    app = create_app(engine, api_key="k", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.post("/sync/tick", headers={"X-API-Key": "k"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["jira_to_vk_created"] == 1
    assert body["total_writes"] >= 1
    # Confirm side effect actually happened on the fake.
    assert any(c["title"] == "[SCRUM-700] x" for c in mcp.created_calls)


# ----- /sync/tick/{key} happy path -----


def test_tick_for_key_routes_targeted_sync() -> None:
    """POST /sync/tick/{key} hits get_issue, not /search/jql."""
    search_calls: list[str] = []
    get_issue_calls: list[str] = []

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
                            "summary": "y",
                            "status": {"name": "To Do"},
                            "description": None,
                        },
                    },
                )
        return httpx.Response(404)

    mcp = FakeMcpClient()
    engine, jira = make_engine(handler, mcp=mcp)
    app = create_app(engine, api_key="k", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.post("/sync/tick/SCRUM-800", headers={"X-API-Key": "k"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["jira_to_vk_created"] == 1
    # Targeted path must NOT trigger a /search/jql sweep.
    assert search_calls == []
    assert get_issue_calls == ["SCRUM-800"]


def test_tick_for_key_requires_api_key() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="needed", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.post("/sync/tick/SCRUM-1")

    assert resp.status_code == 401


# ----- /sync/tick/{key} input validation -----


@pytest.mark.parametrize("bad_key", [
    "../etc/passwd",          # path traversal attempt
    "scrum-1",                # lowercase project (regex requires upper)
    "SCRUM 1",                # space breaks the dash format
    "SCRUM-",                 # missing number
    "-1",                     # missing project
    "SCRUM-0",                # number must start with non-zero
    "A-1",                    # project too short (2-char minimum)
    "SCRUM-" + "9" * 12,      # number well past 9 digits
])
def test_tick_for_key_rejects_malformed_keys(bad_key: str) -> None:
    """Path regex must 422 anything that isn't shape <PROJECT>-<NUM>.

    Covers (a) traversal-shaped strings even though jira_client encodes
    safely, (b) garbage that would waste a Jira round-trip, and
    (c) unbounded length that would amplify resource use behind the lock.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        # If validation worked, no Jira call should happen at all.
        return httpx.Response(500, text="validation should have rejected")

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.post(f"/sync/tick/{bad_key}")

    # FastAPI Path validation returns 422 (or 404 if the path doesn't match
    # any route — '../etc/passwd' contains slashes so the route doesn't
    # match at all). Both outcomes prove the engine never ran.
    assert resp.status_code in (404, 422)


# ----- constant-time API key compare -----


def test_tick_rejects_wrong_length_api_key() -> None:
    """Wrong key of different length still 401s — no length-leak side channel.

    `hmac.compare_digest` rejects mismatched lengths in constant time and
    we wrap None as '' before comparing, so a missing header and a wrong
    short key both look the same to the caller.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="correct-secret-32chars-XXXX", enable_poll_task=False)

    with jira, TestClient(app) as client:
        no_header = client.post("/sync/tick")
        short = client.post("/sync/tick", headers={"X-API-Key": "x"})
        long = client.post("/sync/tick", headers={"X-API-Key": "x" * 200})
        wrong_same_length = client.post(
            "/sync/tick",
            headers={"X-API-Key": "wrongsecret-32chars-XXXX-yyyy"},
        )

    assert no_header.status_code == 401
    assert short.status_code == 401
    assert long.status_code == 401
    assert wrong_same_length.status_code == 401


# ----- poll_wake interrupts cold sleep -----


def test_tick_sets_poll_wake_when_writes_happened() -> None:
    """A successful HTTP tick must signal the poll loop to re-evaluate.

    Without this, an in-progress 300s cold sleep would keep the loop cold
    even though `last_write_at` just became hot. We test the side effect
    on `poll_wake` directly because exercising the actual cancellation
    path needs a real running loop and is covered by integration.
    """
    issues = [
        {
            "key": "SCRUM-900",
            "fields": {
                "summary": "x",
                "status": {"name": "To Do"},
                "description": None,
            },
        }
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": issues})
        return httpx.Response(404)

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.post("/sync/tick")
        # Inspect the lifespan-built state — it lives on app.state.sync.
        state = app.state.sync

    assert resp.status_code == 200
    # The tick wrote (created the SCRUM-900 mirror), so poll_wake should
    # be set by the handler before the response returned.
    assert state.poll_wake.is_set()


def test_tick_does_not_set_poll_wake_on_no_op() -> None:
    """An idle tick (no writes) leaves poll_wake unchanged.

    We don't want to wake the loop spuriously — the whole point of cold
    cadence is to quiet down when nothing's happening.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": []})
        return httpx.Response(404)

    engine, jira = make_engine(handler)
    app = create_app(engine, api_key="", enable_poll_task=False)

    with jira, TestClient(app) as client:
        resp = client.post("/sync/tick")
        state = app.state.sync

    assert resp.status_code == 200
    assert not state.poll_wake.is_set()


# ----- helper coverage -----


def test_report_to_dict_includes_total_writes() -> None:
    """Serialization helper must include the derived total_writes field."""
    r = TickReport(jira_to_vk_created=2, vk_to_jira_transitioned=1)
    out = _report_to_dict(r)
    assert out["jira_to_vk_created"] == 2
    assert out["vk_to_jira_transitioned"] == 1
    assert out["total_writes"] == 3
