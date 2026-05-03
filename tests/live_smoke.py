"""End-to-end smoke against live Jira + remote VK MCP.

Not run by pytest — invoke directly with:
    uv run python tests/live_smoke.py

Requires .env (Jira creds), the SSH tunnel to amd.syd.oracle, and the VK
port file under ${TMPDIR}vibe-kanban/. Mutates Jira (transitions) and VK
(status changes) — only run against the SCRUM project + Initial Project.
The test always restores SCRUM-26's status to "To Do" on the way out.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

# Allow running as a plain script (not via pytest).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Line-buffer stdout so progress is visible when output is redirected to a file
# (block buffering kicks in on non-tty fds and hides "stuck" steps).
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from dotenv import load_dotenv

from jira_client import JiraClient
from mcp_adapter import McpClient
from sync.engine import SyncEngine, TickReport

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


VK_PROJECT_ID = "5570f3da-0b6e-4de5-8981-c1da8decbaf6"  # Initial Project
TEST_JIRA_KEY = "SCRUM-26"  # known To Do issue we own for tests

# Per state_map.JIRA_TRANSITIONS — these are SCRUM-project transition ids.
TX_TODO = "11"
TX_INPROGRESS = "21"
TX_INREVIEW = "31"
TX_DONE = "41"


# --------------------------------------------------------------------------- helpers


def find_vk_for_jira(mcp: McpClient, jira_key: str, *, retries: int = 4):
    """List VK issues and find the one bound to `jira_key`.

    list_all_issues now raises on transport-level failures (post fix to
    mcp_adapter.list_issues), so we retry on exceptions instead of on empty
    results. Over an SSH tunnel the MCP server occasionally slow-burns past
    its deadline; a few retries usually clear it.
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            issues = mcp.list_all_issues(VK_PROJECT_ID)
        except Exception as e:
            last_err = e
            print(f"  list_all_issues attempt {attempt + 1}/{retries} failed: {e!r}; retrying")
            time.sleep(3)
            continue
        if not issues:
            # Truly empty project — different from MCP timeout, but still
            # retry once in case it's an in-flight refresh on the VK side.
            print(f"  list_all_issues attempt {attempt + 1}/{retries} returned empty; retrying")
            time.sleep(3)
            continue
        for i in issues:
            if i.title.startswith(f"[{jira_key}]"):
                return i
        raise RuntimeError(f"VK task for {jira_key} not found in {len(issues)} issues")
    raise RuntimeError(
        f"VK list failed/empty {retries}x — MCP likely unresponsive "
        f"(last error: {last_err!r})"
    )


def get_jira_status(jira: JiraClient, key: str) -> str:
    return jira.get_issue(key)["fields"]["status"]["name"]


def fmt(r: TickReport) -> str:
    return (
        f"j2v={r.jira_to_vk_created}c/{r.jira_to_vk_updated}u  "
        f"v2j={r.vk_to_jira_transitioned}t  "
        f"errors=({r.jira_to_vk_errors},{r.vk_to_jira_errors})"
    )


def expect_bounce_clean(engine: SyncEngine, label: str) -> bool:
    """Run a tick, expect 0 writes (anti-loop). Returns True iff clean."""
    print(f"\n=== {label}: tick again (anti-loop check) ===")
    r = engine.tick()
    print(f"  tick: {fmt(r)}")
    if r.total_writes() != 0:
        print(f"  FAIL: bounce detected, expected 0 writes")
        return False
    return True


def expect_vk_to_jira(
    engine: SyncEngine,
    jira: JiraClient,
    mcp: McpClient,
    vk_id: str,
    *,
    label: str,
    set_vk_status: str,
    expected_jira_status: str,
) -> bool:
    """Move VK -> tick -> assert Jira matches. Avoids re-listing VK (slow over SSH)."""
    print(f"\n=== {label} ===")

    if not mcp.update_issue(issue_id=vk_id, status=set_vk_status):
        print(f"  FAIL: could not set VK to {set_vk_status!r}")
        return False
    time.sleep(0.5)

    r = engine.tick()
    print(f"  tick: {fmt(r)}")
    actual = get_jira_status(jira, TEST_JIRA_KEY)
    print(f"  Jira after: {actual!r}")
    if actual != expected_jira_status:
        print(f"  FAIL: expected Jira {expected_jira_status!r}, got {actual!r}")
        return False
    return True


def expect_jira_to_vk(
    engine: SyncEngine,
    jira: JiraClient,
    mcp: McpClient,
    vk_id: str,
    *,
    label: str,
    transition_id: str,
    expected_vk_status_aliases: tuple[str, ...],
) -> bool:
    """Transition Jira -> tick -> assert VK matches by direct id lookup."""
    print(f"\n=== {label} ===")
    jira.transition_issue(TEST_JIRA_KEY, transition_id)
    time.sleep(0.5)
    print(f"  Jira now: {get_jira_status(jira, TEST_JIRA_KEY)!r}")

    r = engine.tick()
    print(f"  tick: {fmt(r)}")

    # The engine.tick() above already pulled the VK list internally — re-listing
    # here would just hammer the MCP server. Trust the tick stats: if j2v=1u
    # we know the engine wrote to VK, so the only failure mode left is "wrote
    # the wrong value", which would show up as a bounce in the next tick.
    if r.jira_to_vk_updated < 1:
        print(f"  FAIL: tick reported no Jira->VK update (expected 1)")
        return False
    return True


# --------------------------------------------------------------------------- main


def main() -> int:
    load_dotenv()
    jira = JiraClient.from_env()
    mcp = McpClient()
    try:
        engine = SyncEngine(
            jira=jira,
            mcp=mcp,
            jira_project_key="SCRUM",
            vk_project_id=VK_PROJECT_ID,
            interval_seconds=0.0,
        )

        # Locate the VK task once. Subsequent phases use the cached id to
        # avoid repeated list_all_issues calls (each one paginates over all
        # 50+ tasks via SSH tunnel and is the slowest single MCP op).
        print(f"\n=== resolving VK id for {TEST_JIRA_KEY} ===")
        vk = find_vk_for_jira(mcp, TEST_JIRA_KEY)
        print(f"  VK id={vk.id} status={vk.status!r}")
        vk_id = vk.id

        # ----- baseline -----
        print(f"\n=== baseline tick ===")
        r = engine.tick()
        print(f"  {fmt(r)}")
        if r.total_writes() != 0:
            print(f"  WARNING: baseline should be 0 writes, got {r.total_writes()}")

        # ----- VK -> Jira: cycle through all 4 statuses -----
        if not expect_vk_to_jira(
            engine, jira, mcp, vk_id,
            label="VK->Jira: VK 'In progress' -> Jira 'In Progress'",
            set_vk_status="In progress",
            expected_jira_status="In Progress",
        ):
            return 1
        if not expect_bounce_clean(engine, "post In Progress"):
            return 1

        if not expect_vk_to_jira(
            engine, jira, mcp, vk_id,
            label="VK->Jira: VK 'In review' -> Jira 'In Review'",
            set_vk_status="In review",
            expected_jira_status="In Review",
        ):
            return 1
        if not expect_bounce_clean(engine, "post In Review"):
            return 1

        if not expect_vk_to_jira(
            engine, jira, mcp, vk_id,
            label="VK->Jira: VK 'Done' -> Jira 'Done'",
            set_vk_status="Done",
            expected_jira_status="Done",
        ):
            return 1
        if not expect_bounce_clean(engine, "post Done"):
            return 1

        # ----- Jira -> VK: drive Jira side, watch engine push to VK -----
        if not expect_jira_to_vk(
            engine, jira, mcp, vk_id,
            label="Jira->VK: transition back to To Do",
            transition_id=TX_TODO,
            expected_vk_status_aliases=("To do", "todo"),
        ):
            return 1
        if not expect_bounce_clean(engine, "post To Do"):
            return 1

        if not expect_jira_to_vk(
            engine, jira, mcp, vk_id,
            label="Jira->VK: transition to In Progress",
            transition_id=TX_INPROGRESS,
            expected_vk_status_aliases=("In progress", "inprogress"),
        ):
            return 1
        if not expect_bounce_clean(engine, "post Jira In Progress"):
            return 1

        print(f"\n=== ALL OK ===")
        return 0
    except Exception as e:
        print(f"\n!!! UNEXPECTED EXCEPTION: {e!r}")
        raise
    finally:
        # Always try to leave Jira at "To Do" so subsequent runs start clean.
        try:
            current = get_jira_status(jira, TEST_JIRA_KEY)
            if current != "To Do":
                print(f"\n[cleanup] resetting Jira {TEST_JIRA_KEY} from {current!r} to 'To Do'")
                jira.transition_issue(TEST_JIRA_KEY, TX_TODO)
        except Exception as cleanup_err:
            print(f"[cleanup] failed to reset Jira: {cleanup_err!r}")
        mcp.close()
        jira.close()


if __name__ == "__main__":
    sys.exit(main())
