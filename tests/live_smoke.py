"""End-to-end smoke against live Jira + remote VK MCP.

Not run by pytest — invoke directly with:
    uv run python tests/live_smoke.py

Requires .env (Jira creds), the SSH tunnel to amd.syd.oracle, and the VK
port file under ${TMPDIR}vibe-kanban/. Mutates Jira (transitions) and VK
(status changes) — only run against the SCRUM project + Initial Project.
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
from sync.engine import SyncEngine

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


VK_PROJECT_ID = "5570f3da-0b6e-4de5-8981-c1da8decbaf6"  # Initial Project
TEST_JIRA_KEY = "SCRUM-26"  # known To Do issue


def find_vk_for_jira(mcp: McpClient, jira_key: str, *, retries: int = 4):
    """List VK issues and find the one bound to `jira_key`.

    list_issues swallows MCP timeouts and returns `[]`, indistinguishable from
    "really empty". Over an SSH tunnel the MCP server occasionally slow-burns
    past its 60s deadline. Retry on empty result, since the project is known
    to have many tasks.
    """
    for attempt in range(retries):
        issues = mcp.list_all_issues(VK_PROJECT_ID)
        if not issues:
            print(f"  list_all_issues attempt {attempt + 1}/{retries} returned empty (likely MCP timeout); retrying")
            time.sleep(3)
            continue
        for i in issues:
            if i.title.startswith(f"[{jira_key}]"):
                return i
        # Non-empty list but jira_key not present — that's a real "not found"
        raise RuntimeError(f"VK task for {jira_key} not found in {len(issues)} issues")
    raise RuntimeError(f"VK list returned empty {retries}x — MCP likely unresponsive")


def get_jira_status(jira: JiraClient, key: str) -> str:
    issue = jira.get_issue(key)
    return issue["fields"]["status"]["name"]


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

        print(f"=== baseline tick ===")
        r = engine.tick()
        print(f"  j2v={r.jira_to_vk_created}c/{r.jira_to_vk_updated}u  v2j={r.vk_to_jira_transitioned}t  errors=({r.jira_to_vk_errors},{r.vk_to_jira_errors})")
        if r.total_writes() != 0:
            print(f"  WARNING: baseline should be 0 writes, got {r.total_writes()}")

        # ----- VK -> Jira test -----
        print(f"\n=== VK->Jira: move {TEST_JIRA_KEY} in VK to In progress ===")
        vk = find_vk_for_jira(mcp, TEST_JIRA_KEY)
        print(f"  VK before: {vk.status!r}")
        jira_before = get_jira_status(jira, TEST_JIRA_KEY)
        print(f"  Jira before: {jira_before!r}")

        ok = mcp.update_issue(issue_id=vk.id, status="In progress")
        if not ok:
            print(f"  FAIL: could not update VK status")
            return 1
        time.sleep(0.5)

        r = engine.tick()
        print(f"  tick: j2v={r.jira_to_vk_updated}u  v2j={r.vk_to_jira_transitioned}t")
        jira_after = get_jira_status(jira, TEST_JIRA_KEY)
        print(f"  Jira after: {jira_after!r}")
        if jira_after != "In Progress":
            print(f"  FAIL: expected Jira 'In Progress', got {jira_after!r}")
            return 1

        # ----- bounce test -----
        print(f"\n=== bounce check: tick again ===")
        r = engine.tick()
        print(f"  tick: j2v={r.jira_to_vk_updated}u  v2j={r.vk_to_jira_transitioned}t")
        if r.total_writes() != 0:
            print(f"  FAIL: bounce detected, expected 0 writes")
            return 1

        # ----- Jira -> VK test -----
        print(f"\n=== Jira->VK: transition {TEST_JIRA_KEY} in Jira back to To Do ===")
        # transition_id 11 = To Do (per state_map JIRA_TRANSITIONS)
        jira.transition_issue(TEST_JIRA_KEY, "11")
        time.sleep(0.5)
        jira_after = get_jira_status(jira, TEST_JIRA_KEY)
        print(f"  Jira now: {jira_after!r}")

        r = engine.tick()
        print(f"  tick: j2v={r.jira_to_vk_updated}u  v2j={r.vk_to_jira_transitioned}t")
        vk_after = find_vk_for_jira(mcp, TEST_JIRA_KEY)
        print(f"  VK after: {vk_after.status!r}")
        if vk_after.status not in ("To do", "todo"):
            print(f"  FAIL: expected VK 'To do', got {vk_after.status!r}")
            return 1

        # ----- bounce check #2 -----
        print(f"\n=== bounce check #2: tick again ===")
        r = engine.tick()
        print(f"  tick: j2v={r.jira_to_vk_updated}u  v2j={r.vk_to_jira_transitioned}t")
        if r.total_writes() != 0:
            print(f"  FAIL: bounce detected, expected 0 writes")
            return 1

        print(f"\n=== ALL OK ===")
        return 0
    finally:
        mcp.close()
        jira.close()


if __name__ == "__main__":
    sys.exit(main())
