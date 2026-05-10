"""FastAPI HTTP API for scrumai-sync.

Exposes the bidirectional sync engine over HTTP so a Jira Forge plugin can
trigger immediate sync after user actions. Layered on top of `SyncEngine` —
the same engine that backs `python main.py sync`. There is exactly one
engine per server process; multiple uvicorn workers would each own their
own anti-loop ledger and adaptive-polling state, which would defeat both.
Always run with `--workers 1`.

Endpoints
---------
GET  /health
    Liveness probe for nginx + uptime monitors. Always 200, no auth.

POST /sync/tick
    Run a full Jira<->VK sweep. Returns TickReport JSON.

POST /sync/tick/{jira_key}
    Targeted sync for a single Jira key. Forge calls this after a user
    action so the kanban reflects the change in <1s instead of waiting
    for the next polling tick.

GET  /vk/issue/{jira_key}
    Read-only lookup for the VK card bound to one Jira key. Forge uses this
    to show the real card title/status in the Jira issue panel.

Auth: `/sync/*` endpoints require `X-API-Key: <api_key>` iff the server
was started with a non-empty `api_key`. /health is intentionally
unauthenticated so probes don't need credentials.

Concurrency: one `asyncio.Lock` serializes ticks across HTTP requests AND
the background polling task. Overlapping ticks would race the engine's
MirrorLedger and write caches; the lock keeps the engine sequential while
/health stays responsive.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field

from fastapi import FastAPI, Header, HTTPException, Path, status
from fastapi.responses import JSONResponse

from sync.engine import SyncEngine, TickReport
from sync.jira_to_vk import VkIssueSnapshot

# Jira issue keys are `<PROJECT>-<NUMBER>`. Project keys are uppercase
# letters/digits/underscores starting with a letter, length 2-16 in practice;
# numbers are 1-9 digits (Jira's documented max is much lower than this cap
# but we leave headroom). Used for FastAPI Path validation so we don't pass
# unbounded strings to Jira/MCP from the public endpoint.
_JIRA_KEY_RE = r"^[A-Z][A-Z0-9_]{1,15}-[1-9][0-9]{0,8}$"
_JIRA_KEY_PATTERN = re.compile(_JIRA_KEY_RE)

logger = logging.getLogger(__name__)


@dataclass
class ServerState:
    """Mutable state shared between request handlers and the poll task."""

    engine: SyncEngine
    api_key: str
    tick_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set by `/sync/*` handlers after a write so the poll loop can interrupt
    # an in-progress cold sleep and re-evaluate `next_interval_seconds()`.
    # Without this, an HTTP-triggered hot transition couldn't shorten a 300s
    # cold sleep already in flight, breaking the adaptive-polling promise.
    poll_wake: asyncio.Event = field(default_factory=asyncio.Event)
    poll_task: asyncio.Task[None] | None = None
    started_at_monotonic: float = 0.0


def _check_api_key(state: ServerState, x_api_key: str | None) -> None:
    """Reject requests missing/wrong key when an api_key is configured.

    Empty `api_key` means "no auth" — used in tests and for local dev. Demo
    deployments should always pass a real key via env.

    Uses `hmac.compare_digest` to avoid leaking the configured key one byte
    at a time via response-time differences (CWE-208). The cost over a real
    `==` is negligible for ~32-byte hex secrets.
    """
    if not state.api_key:
        return
    # `compare_digest` rejects None and mismatched-length inputs in constant
    # time relative to the configured key, so we coerce missing headers to
    # an empty string rather than short-circuiting on `None`.
    if not hmac.compare_digest(x_api_key or "", state.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-API-Key",
        )


def _report_to_dict(report: TickReport) -> dict[str, int]:
    """JSON-friendly view of a TickReport. `asdict` plus the derived total."""
    return {**asdict(report), "total_writes": report.total_writes()}


def _vk_issue_to_dict(issue_key: str, issue: VkIssueSnapshot | None) -> dict[str, object]:
    if issue is None:
        return {"issueKey": issue_key, "found": False}

    raw = asdict(issue)
    return {
        "issueKey": issue_key,
        "found": True,
        "vkIssue": {
            "id": raw["id"],
            "simpleId": raw["simple_id"],
            "title": raw["title"],
            "status": raw["status"],
            "priority": raw["priority"],
        },
    }


async def _poll_loop(state: ServerState) -> None:
    """Background polling — adaptive interval, holds the same lock as HTTP.

    Uses `engine.next_interval_seconds()` so a recent write (HTTP-triggered
    or polled) keeps the loop hot for `hot_window_seconds`, then drops to
    cold cadence. `asyncio.to_thread` offloads the blocking sync engine
    work so /health and other requests stay responsive while a tick runs.

    The sleep is cancellable via `state.poll_wake`: HTTP handlers set the
    event after a write so an in-progress cold sleep (potentially 300s)
    doesn't suppress the freshly-hot interval. Without this, a Forge call
    could land mid-cold-sleep and the next background tick would still be
    delayed by up to `cold_interval` instead of `hot_interval`.
    """
    # Fire the first tick immediately. With `last_write_at=None` on a fresh
    # process, `next_interval_seconds()` returns the cold interval (300s),
    # so without this pre-set the very first `wait_for` would sit idle for
    # up to 5 minutes before any sync — defeating the polling safety net
    # right when it matters most (process restart, recovery from crash).
    # Pre-setting the event causes the first iteration's `wait_for` to
    # return immediately; `clear()` re-arms it for normal cadence.
    state.poll_wake.set()
    while True:
        interval = state.engine.next_interval_seconds()
        # `wait_for(event.wait())` returns when the event is set OR the
        # timeout expires. Either way we drop into the tick below; clearing
        # the event afterwards re-arms it for the next iteration.
        try:
            await asyncio.wait_for(state.poll_wake.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            logger.info("poll loop cancelled")
            return
        state.poll_wake.clear()

        async with state.tick_lock:
            try:
                report = await asyncio.to_thread(state.engine.tick)
            except Exception as e:
                logger.exception("poll tick crashed: %s", e)
                continue

            if report.total_writes() > 0:
                logger.info(
                    "poll tick: writes=%d (j2v_c=%d j2v_u=%d v2j_t=%d)",
                    report.total_writes(),
                    report.jira_to_vk_created,
                    report.jira_to_vk_updated,
                    report.vk_to_jira_transitioned,
                )


def create_app(
    engine: SyncEngine,
    *,
    api_key: str = "",
    enable_poll_task: bool = True,
) -> FastAPI:
    """Build the FastAPI app. No HTTP server starts here — uvicorn drives it.

    Tests pass `enable_poll_task=False` so the lifespan startup doesn't fire
    a real polling task that would race assertions. Production leaves it
    True so the loop runs in-process alongside HTTP.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Build state inside lifespan so the asyncio.Lock binds to the
        # running loop (FastAPI's TestClient creates its own loop per test).
        state = ServerState(
            engine=engine,
            api_key=api_key,
            started_at_monotonic=time.monotonic(),
        )
        _app.state.sync = state

        if enable_poll_task:
            state.poll_task = asyncio.create_task(_poll_loop(state))

        try:
            yield
        finally:
            if state.poll_task is not None:
                state.poll_task.cancel()
                try:
                    await state.poll_task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(
        title="scrumai-sync",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        # No auth: probes by reverse proxy + uptime monitors.
        return {"status": "ok"}

    @app.post("/sync/tick")
    async def tick(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> JSONResponse:
        state: ServerState = app.state.sync
        _check_api_key(state, x_api_key)
        async with state.tick_lock:
            report = await asyncio.to_thread(state.engine.tick)
        if report.total_writes() > 0:
            # Wake the background poll loop so it re-evaluates the interval
            # against the freshly-hot `last_write_at` instead of riding out
            # whatever cold sleep was in flight.
            state.poll_wake.set()
        return JSONResponse(_report_to_dict(report))

    @app.post("/sync/tick/{jira_key}")
    async def tick_for_key(
        jira_key: str = Path(
            ...,
            pattern=_JIRA_KEY_RE,
            description="Jira issue key, e.g. SCRUM-123",
        ),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> JSONResponse:
        state: ServerState = app.state.sync
        _check_api_key(state, x_api_key)
        async with state.tick_lock:
            report = await asyncio.to_thread(state.engine.tick_for_key, jira_key)
        if report.total_writes() > 0:
            state.poll_wake.set()
        return JSONResponse(_report_to_dict(report))

    @app.get("/vk/issue/{jira_key}")
    async def get_vk_issue(
        jira_key: str = Path(
            ...,
            pattern=_JIRA_KEY_RE,
            description="Jira issue key, e.g. SCRUM-123",
        ),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> JSONResponse:
        state: ServerState = app.state.sync
        _check_api_key(state, x_api_key)
        async with state.tick_lock:
            issue = await asyncio.to_thread(state.engine.get_vk_issue_for_key, jira_key)
        return JSONResponse(_vk_issue_to_dict(jira_key, issue))

    return app


def run_server(
    *,
    host: str,
    port: int,
    jira_project_key: str,
    vk_project_name: str,
    api_key: str,
    hot_interval: float,
    cold_interval: float,
    hot_window: float,
) -> None:
    """Production entry: bootstrap engine + serve via uvicorn.

    Resolves the VK project by name (same as `python main.py sync`), wires
    up the engine with adaptive polling intervals, and runs uvicorn until
    SIGTERM/SIGINT. Closes Jira/MCP clients in a `finally` after uvicorn
    exits so a graceful shutdown reclaims resources.
    """
    import uvicorn
    from dotenv import load_dotenv

    from jira_client import JiraClient
    from mcp_adapter import McpClient

    load_dotenv()

    jira = JiraClient.from_env()
    mcp = McpClient()

    cleanup: list[Callable[[], None]] = []
    cleanup.append(lambda: jira.close())
    cleanup.append(lambda: mcp.close())

    try:
        organizations = mcp.list_organizations()
        if not organizations:
            raise RuntimeError(
                "no Vibe Kanban organizations visible. "
                "Confirm VIBE_BACKEND_URL and the MCP backend are reachable."
            )

        org = organizations[0]
        projects = mcp.list_projects(org.id)
        target = next((p for p in projects if p.name == vk_project_name), None)
        if target is None:
            raise RuntimeError(
                f"VK project {vk_project_name!r} not found. "
                f"Available: {[p.name for p in projects]}"
            )

        engine = SyncEngine(
            jira=jira,
            mcp=mcp,
            jira_project_key=jira_project_key,
            vk_project_id=target.id,
            hot_interval_seconds=hot_interval,
            cold_interval_seconds=cold_interval,
            hot_window_seconds=hot_window,
        )

        app = create_app(engine, api_key=api_key)

        if not api_key:
            # Loud, on every startup — silent dev defaults are how we end up
            # with a publicly exposed unauthenticated /sync/* endpoint
            # because someone forgot to set SCRUMAI_API_KEY in /etc/scrumai/sync.env.
            # Loopback bind doesn't help if the operator passes --host 0.0.0.0.
            logger.warning(
                "SCRUMAI_API_KEY is empty — /sync/* endpoints are UNAUTHENTICATED. "
                "This is fine on a loopback dev box but NEVER for production. "
                "Set SCRUMAI_API_KEY in the env to require X-API-Key on writes."
            )

        logger.info(
            "scrumai-sync starting: host=%s port=%d project=%s vk=%r api_key_set=%s",
            host, port, jira_project_key, vk_project_name, bool(api_key),
        )
        uvicorn.run(app, host=host, port=port, workers=1, log_level="info")
    finally:
        for fn in cleanup:
            try:
                fn()
            except Exception:
                pass
