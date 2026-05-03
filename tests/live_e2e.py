"""Final acceptance: real Jira ticket → sync → VK → status round-trip → cleanup.

Not run by pytest — invoke directly:

    VIBE_BACKEND_URL=http://127.0.0.1:3000 uv run python tests/live_e2e.py
    # or skip cleanup so the ticket stays in the backlog for manual inspection:
    KEEP_TICKETS=1 VIBE_BACKEND_URL=http://127.0.0.1:3000 \
        uv run python tests/live_e2e.py

Requires:
  - .env with JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
  - SSH tunnel to amd.syd.oracle: `ssh -f -N -L 3000:localhost:3000 amd.syd.oracle`
  - VIBE_BACKEND_URL=http://127.0.0.1:3000 — without this the vibe-kanban MCP
    server reads a stale local port file and connects to the wrong backend
    (returns 0 issues silently, masquerading as an empty project).

Mutates:
  - Creates one Jira Story in SCRUM (then deletes it unless KEEP_TICKETS=1)
  - Creates one VK task in the configured project (then deletes it likewise)

Phases:
  1. create Jira Story in 'To Do' — wait until /search/jql indexes it (~2-3s lag).
  2. tick J->V — assert VK task created with `[<KEY>]` title prefix, status 'todo'.
  3. set VK to 'In progress', tick V->J — assert Jira transitioned to 'In Progress';
     bounce-check tick — assert 0 writes (anti-loop).
  4. transition Jira to 'Done', tick J->V — assert VK reaches 'Done';
     bounce-check tick — assert 0 writes.
  5. cleanup (delete VK + Jira) unless KEEP_TICKETS=1.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
JIRA_PROJECT_KEY = "SCRUM"
TX_TODO = "11"
TX_INPROGRESS = "21"
TX_INREVIEW = "31"
TX_DONE = "41"


def fmt(r: TickReport) -> str:
    return (
        f"j2v={r.jira_to_vk_created}c/{r.jira_to_vk_updated}u  "
        f"v2j={r.vk_to_jira_transitioned}t  "
        f"errors=({r.jira_to_vk_errors},{r.vk_to_jira_errors})"
    )


def wait_for_jira_search(jira: JiraClient, key: str, *, retries: int = 15) -> bool:
    """Poll search until the new issue shows up. /search/jql is Lucene-backed
    with ~2-3s indexing lag; the engine uses search, so we must wait before
    triggering a tick.
    """
    for attempt in range(retries):
        results = jira.search_issues_by_project(JIRA_PROJECT_KEY, limit=50)
        if any(r.get("key") == key for r in results):
            return True
        time.sleep(1)
    return False


def find_vk_for_jira(mcp: McpClient, jira_key: str, *, retries: int = 4):
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            issues = mcp.list_all_issues(VK_PROJECT_ID)
        except Exception as e:
            last_err = e
            print(f"  list attempt {attempt + 1}/{retries} failed: {e!r}; retrying")
            time.sleep(3)
            continue
        for i in issues:
            if i.title.startswith(f"[{jira_key}]"):
                return i
        print(f"  list attempt {attempt + 1}/{retries}: {len(issues)} issues, no match yet; retrying")
        time.sleep(3)
    raise RuntimeError(
        f"VK task for {jira_key} not found after {retries} attempts (last err: {last_err!r})"
    )


def get_jira_status(jira: JiraClient, key: str) -> str:
    return jira.get_issue(key)["fields"]["status"]["name"]


def main() -> int:
    load_dotenv()
    base_url = os.environ.get("JIRA_BASE_URL", "")
    keep_tickets = bool(os.environ.get("KEEP_TICKETS"))

    jira = JiraClient.from_env()
    mcp = McpClient()

    jira_key: str | None = None
    vk_id: str | None = None

    try:
        engine = SyncEngine(
            jira=jira,
            mcp=mcp,
            jira_project_key=JIRA_PROJECT_KEY,
            vk_project_id=VK_PROJECT_ID,
            interval_seconds=0.0,
        )

        # ----- phase 1: create Jira Story -----
        print("\n=== phase 1: create Jira Story ===")
        summary = f"[E2E sync test] {time.strftime('%Y-%m-%d %H:%M:%S')}"
        created = jira.create_issue(
            project_key=JIRA_PROJECT_KEY,
            summary=summary,
            issue_type_name="Story",
            description="Created by tests/live_e2e.py to verify the J↔V sync engine.",
            labels=["e2e", "scrumai-sync-test"],
        )
        jira_key = created["key"]
        print(f"  created Jira {jira_key}: {summary}")
        print(f"  URL: {base_url}/browse/{jira_key}")
        status = get_jira_status(jira, jira_key)
        print(f"  Jira status: {status!r}")
        if status != "To Do":
            print(f"  WARNING: expected 'To Do' on a freshly created Story, got {status!r}")

        # Jira /search/jql has ~2-3s indexing lag; the engine uses search,
        # so the freshly created issue is invisible to it until indexed.
        print("  waiting for Jira search index to catch up...")
        if not wait_for_jira_search(jira, jira_key):
            print(f"  FAIL: {jira_key} never appeared in search after creation")
            return 1
        print(f"  {jira_key} now visible in search")

        # ----- phase 2: tick J->V should create the VK task -----
        print("\n=== phase 2: tick (J→V should create VK task) ===")
        r = engine.tick()
        print(f"  tick: {fmt(r)}")
        if r.jira_to_vk_created < 1:
            print(f"  FAIL: J→V did not create VK task (created={r.jira_to_vk_created})")
            return 1

        time.sleep(0.5)
        vk = find_vk_for_jira(mcp, jira_key)
        vk_id = vk.id
        print(f"  found VK id={vk.id} title={vk.title!r} status={vk.status!r}")

        # ----- phase 3: VK→Jira: set VK 'In progress' -> Jira 'In Progress' -----
        print("\n=== phase 3: VK→Jira (set VK 'In progress') ===")
        if not mcp.update_issue(issue_id=vk_id, status="In progress"):
            print("  FAIL: could not set VK to 'In progress'")
            return 1
        time.sleep(0.5)

        r = engine.tick()
        print(f"  tick: {fmt(r)}")
        if r.vk_to_jira_transitioned < 1:
            print(f"  FAIL: V→J did not transition (transitioned={r.vk_to_jira_transitioned})")
            return 1
        actual = get_jira_status(jira, jira_key)
        print(f"  Jira after: {actual!r}")
        if actual != "In Progress":
            print(f"  FAIL: expected Jira 'In Progress', got {actual!r}")
            return 1

        # anti-loop tick — zero writes expected
        r = engine.tick()
        print(f"  bounce-check tick: {fmt(r)}")
        if r.total_writes() != 0:
            print(f"  FAIL: bounce detected ({r.total_writes()} writes)")
            return 1

        # ----- phase 4: Jira→VK: transition Jira 'Done' -> VK 'Done' -----
        print("\n=== phase 4: Jira→VK (transition Jira to 'Done') ===")
        jira.transition_issue(jira_key, TX_DONE)
        time.sleep(0.5)
        actual = get_jira_status(jira, jira_key)
        print(f"  Jira now: {actual!r}")
        if actual != "Done":
            print(f"  FAIL: Jira didn't transition to 'Done'; got {actual!r}")
            return 1

        r = engine.tick()
        print(f"  tick: {fmt(r)}")
        if r.jira_to_vk_updated < 1:
            print(f"  FAIL: J→V did not update VK (updated={r.jira_to_vk_updated})")
            return 1

        # confirm VK actually moved by re-listing
        vk_after = next(
            (i for i in mcp.list_all_issues(VK_PROJECT_ID) if i.id == vk_id),
            None,
        )
        if vk_after is None:
            print("  FAIL: VK task disappeared after Jira→VK update")
            return 1
        print(f"  VK after: status={vk_after.status!r}")
        if vk_after.status.lower().replace(" ", "") != "done":
            print(f"  FAIL: expected VK 'Done', got {vk_after.status!r}")
            return 1

        # anti-loop tick
        r = engine.tick()
        print(f"  bounce-check tick: {fmt(r)}")
        if r.total_writes() != 0:
            print(f"  FAIL: bounce detected ({r.total_writes()} writes)")
            return 1

        print("\n=== ALL PHASES OK ===")
        if keep_tickets:
            print(
                f"\n[KEEP_TICKETS=1] leaving Jira {jira_key} and VK task in place "
                f"for manual inspection. Delete with:\n"
                f"  python -c \"from dotenv import load_dotenv; load_dotenv(); "
                f"from jira_client import JiraClient; "
                f"JiraClient.from_env().delete_issue('{jira_key}')\""
            )
        return 0

    except Exception as e:
        print(f"\n!!! UNEXPECTED EXCEPTION: {e!r}")
        raise

    finally:
        if not keep_tickets:
            print("\n=== cleanup ===")
            if vk_id is not None:
                try:
                    ok = mcp.delete_issue(vk_id)
                    print(f"  delete VK {vk_id}: ok={ok}")
                except Exception as e:
                    print(f"  delete VK {vk_id} failed: {e!r}")
            if jira_key is not None:
                try:
                    jira.delete_issue(jira_key)
                    print(f"  delete Jira {jira_key}: ok")
                except Exception as e:
                    print(f"  delete Jira {jira_key} failed: {e!r}")
        mcp.close()
        jira.close()


if __name__ == "__main__":
    sys.exit(main())
