"""Live smoke test against the real Jira Cloud API.

This test hits the real Jira instance configured in `.env`. It creates a
sub-task under SCRUM-27, reads it back, comments + relabels it, and finally
deletes it. Skipped unless RUN_JIRA_SMOKE=1 is set in the environment so it
never runs accidentally in CI / regular `pytest` invocations.

Run with:
    RUN_JIRA_SMOKE=1 uv run pytest tests/test_jira_smoke.py -v -s
"""

from __future__ import annotations

import os
import time
from typing import cast

import pytest
from dotenv import load_dotenv

from jira_client import JiraClient, JiraClientError

# Load .env so JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN are available
load_dotenv()

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_JIRA_SMOKE") != "1",
    reason="Set RUN_JIRA_SMOKE=1 to hit the real Jira API",
)

PROJECT_KEY = "SCRUM"
PARENT_KEY = "SCRUM-27"
SMOKE_LABEL = "scrumai-smoke-test"
UPDATE_LABEL = "scrumai-smoke-updated"


@pytest.fixture
def client() -> JiraClient:
    """Build a real Jira client from .env."""
    c = JiraClient.from_env()
    yield c
    c.close()


def _print(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def test_full_crud_cycle(client: JiraClient) -> None:
    """Walk a full Create / Read / Update / Delete cycle on test data."""
    timestamp = int(time.time())
    summary = f"[SMOKE TEST {timestamp}] CRUD verification"
    created_keys: list[str] = []

    try:
        # ---------- READ (auth + connectivity check) ----------
        _print(f"GET issue {PARENT_KEY}")
        parent = client.get_issue(PARENT_KEY)
        assert parent.get("key") == PARENT_KEY
        parent_fields = cast(dict[str, object], parent.get("fields") or {})
        parent_type = cast(
            dict[str, object], parent_fields.get("issuetype") or {}
        )
        _print(f"  parent type: {parent_type.get('name')}")

        _print(f"GET issue types")
        types = client.get_issue_types()
        subtask_types = [t for t in types if t.get("subtask")]
        assert subtask_types, "Jira has no sub-task issue type"
        _print(
            f"  found {len(types)} issue types, {len(subtask_types)} sub-task type(s)"
        )

        _print(f"GET transitions for {PARENT_KEY}")
        transitions = client.get_transitions(PARENT_KEY)
        assert isinstance(transitions, list)
        _print(f"  {len(transitions)} transitions available")

        _print(f"SEARCH project={PROJECT_KEY} (limit=5)")
        results = client.search_issues_by_project(PROJECT_KEY, limit=5)
        assert isinstance(results, list)
        _print(f"  search returned {len(results)} issues")

        # ---------- CREATE ----------
        _print(f"CREATE sub-task under {PARENT_KEY}")
        result = client.create_subtask(
            parent_key=PARENT_KEY,
            summary=summary,
            description=(
                f"Auto-created by scrumai-prompts/jira_client smoke test "
                f"at unix={timestamp}. Will be deleted at end of test."
            ),
            labels=[SMOKE_LABEL],
        )
        assert "id" in result and "key" in result
        new_key = result["key"]
        created_keys.append(new_key)
        _print(f"  created {new_key} (id={result['id']})")

        # ---------- READ after create ----------
        _print(f"GET created issue {new_key}")
        new_issue = client.get_issue(new_key)
        new_fields = cast(dict[str, object], new_issue.get("fields") or {})
        assert new_issue.get("key") == new_key
        assert new_fields.get("summary") == summary
        labels_after_create = cast(
            list[str], new_fields.get("labels") or []
        )
        assert SMOKE_LABEL in labels_after_create
        _print(
            f"  verified summary, labels={labels_after_create}"
        )

        # ---------- UPDATE: comment ----------
        _print(f"ADD comment to {new_key}")
        comment_body = JiraClient.create_text_comment(
            f"Smoke-test comment at unix={timestamp}."
        )
        client.add_comment(new_key, comment_body)
        _print(f"  comment added")

        # ---------- UPDATE: labels (append) ----------
        _print(f"UPDATE labels on {new_key} (append {UPDATE_LABEL})")
        client.update_labels(new_key, [UPDATE_LABEL])

        verified = client.get_issue(new_key)
        verified_fields = cast(
            dict[str, object], verified.get("fields") or {}
        )
        labels_after_update = cast(
            list[str], verified_fields.get("labels") or []
        )
        _print(f"  labels now: {labels_after_update}")
        assert SMOKE_LABEL in labels_after_update
        assert UPDATE_LABEL in labels_after_update

    finally:
        # ---------- DELETE (cleanup, even if assertions failed) ----------
        for key in created_keys:
            try:
                _print(f"DELETE {key}")
                client.delete_issue(key)
                _print(f"  deleted {key}")
            except JiraClientError as e:
                _print(
                    f"  WARNING: failed to delete {key}: {e}"
                )
