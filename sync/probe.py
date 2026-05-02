"""Phase 0 probe: discover what VK MCP and Jira actually expose.

Run with:
    uv run python -m sync.probe

Output is human-readable — copy the relevant bits into sync_config.toml.
This script only READS, never modifies state on either side.
"""

from __future__ import annotations

import json
import os
import sys
from typing import cast

from dotenv import load_dotenv

from jira_client import JiraClient
from mcp_adapter import McpClient

load_dotenv()


def header(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def probe_jira(project_key: str) -> None:
    header(f"JIRA — project {project_key}")

    with JiraClient.from_env() as jira:
        # Sample a handful of issues to read their actual status names
        issues = jira.search_issues_by_project(project_key, limit=50)
        print(f"sampled {len(issues)} issues")

        statuses: dict[str, int] = {}
        sample_keys_by_status: dict[str, str] = {}
        for issue in issues:
            fields = cast(dict[str, object], issue.get("fields") or {})
            status = cast(dict[str, object], fields.get("status") or {})
            name = cast(str, status.get("name") or "?")
            statuses[name] = statuses.get(name, 0) + 1
            sample_keys_by_status.setdefault(name, cast(str, issue.get("key") or "?"))

        print("\nstatus name → count (sampled):")
        for name, count in sorted(statuses.items()):
            print(f"  {name!r}: {count}")

        print("\ntransitions per status (one sample issue each):")
        for name, key in sorted(sample_keys_by_status.items()):
            try:
                transitions = jira.get_transitions(key)
                pretty = [
                    f"{t.get('id')}→{cast(dict, t.get('to') or {}).get('name')!r}"
                    for t in transitions
                ]
                print(f"  from {name!r} (e.g. {key}): {pretty}")
            except Exception as e:
                print(f"  from {name!r} ({key}): ERROR {e}")

        print("\nissue types:")
        for t in jira.get_issue_types():
            name = t.get("name")
            is_subtask = t.get("subtask")
            print(f"  {name!r} (subtask={is_subtask})")


def probe_vk() -> None:
    header("VK MCP — vibe-kanban")

    client = McpClient()
    try:
        tools = client._tools  # type: ignore[attr-defined]
        print(f"discovered {len(tools)} tools:")
        for t in tools:
            name = t.get("name")
            desc = (t.get("description") or "").strip().split("\n")[0][:120]
            print(f"  {name}  —  {desc}")

        print("\ninput schemas for status/move-relevant tools:")
        for t in tools:
            name = cast(str, t.get("name") or "")
            lname = name.lower()
            if any(
                kw in lname
                for kw in ("update", "status", "move", "transition", "create_issue", "list_issues")
            ):
                schema = t.get("inputSchema") or {}
                print(f"\n  --- {name} ---")
                print(f"  {json.dumps(schema, indent=2, ensure_ascii=False)}")

        # Try to enumerate projects + issue statuses we see in the wild
        orgs = client.list_organizations()
        print(f"\norganizations: {len(orgs)}")
        for org in orgs:
            print(f"  {org.id}  {org.name!r} (personal={org.is_personal})")

        if orgs:
            org_id = orgs[0].id
            projects = client.list_projects(org_id)
            print(f"\nprojects in {orgs[0].name!r}: {len(projects)}")
            for p in projects[:10]:
                print(f"  {p.id}  {p.name!r}")

            for p in projects[:3]:
                issues = client.list_all_issues(p.id, page_size=100)
                statuses: dict[str, int] = {}
                for i in issues:
                    statuses[i.status] = statuses.get(i.status, 0) + 1
                if issues:
                    print(f"\n  status breakdown in project {p.name!r} ({len(issues)} issues):")
                    for s, c in sorted(statuses.items()):
                        print(f"    {s!r}: {c}")
    finally:
        client.close()


def main() -> int:
    project_key = os.environ.get("JIRA_PROJECT_KEY", "SCRUM")
    try:
        probe_jira(project_key)
    except Exception as e:
        print(f"\nJIRA probe failed: {e}", file=sys.stderr)

    try:
        probe_vk()
    except Exception as e:
        print(f"\nVK probe failed: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
