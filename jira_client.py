"""Jira REST API client (Python port of scrumai-forge/src/lib/jira-client.ts).

This is the Plan B local direct-connect client used by the upcoming watcher
and any HTTP-server endpoints. It wraps the standard Jira Cloud Platform REST
API (`/rest/api/3/...`) and the Jira Software (Agile) REST API
(`/rest/agile/1.0/...`) with the same surface area as the Forge version, but
authenticates with email + Atlassian API Token (Basic Auth) instead of the
Forge runtime's app token.

Auth: https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/
"""

from __future__ import annotations

import base64
import os
from typing import Optional, TypedDict, Union, cast
from urllib.parse import quote

import httpx


class ADFTextNode(TypedDict):
    type: str
    text: str


class ADFParagraph(TypedDict):
    type: str
    content: list[ADFTextNode]


class ADFDocument(TypedDict):
    type: str
    version: int
    content: list[ADFParagraph]


# JSON values can be anything - use this rather than `Any` per project policy.
JSONValue = Union[
    None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]
]


class JiraClientError(Exception):
    """Raised when a Jira API call returns a non-2xx response."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        details: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details

    def __str__(self) -> str:
        base = super().__str__()
        if self.status_code is not None:
            return f"{base} (status {self.status_code})"
        return base


def _encode_path_segment(segment: str) -> str:
    """Quote a path segment so issue keys / IDs cannot break the URL.

    Jira keys are typically `PROJ-123` (safe) but we play it safe so callers
    can pass arbitrary ids without thinking about escaping.
    """
    return quote(str(segment), safe="")


class JiraClient:
    """Thin wrapper over the Jira Cloud REST APIs.

    Mirrors `scrumai-forge/src/lib/jira-client.ts`. All write methods raise
    `JiraClientError` on non-2xx responses; read methods return parsed JSON.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required (e.g. https://yoursite.atlassian.net)")
        if not email or not api_token:
            raise ValueError("email and api_token are required for Basic Auth")

        token = base64.b64encode(f"{email}:{api_token}".encode("utf-8")).decode("ascii")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(
        cls, *, transport: Optional[httpx.BaseTransport] = None
    ) -> "JiraClient":
        """Build a client from JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN."""
        base_url = os.environ.get("JIRA_BASE_URL", "")
        email = os.environ.get("JIRA_EMAIL", "")
        api_token = os.environ.get("JIRA_API_TOKEN", "")
        missing = [
            name
            for name, value in (
                ("JIRA_BASE_URL", base_url),
                ("JIRA_EMAIL", email),
                ("JIRA_API_TOKEN", api_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        return cls(
            base_url=base_url, email=email, api_token=api_token, transport=transport
        )

    # ----- context manager / lifecycle -----

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----- internal helpers -----

    def _raise_for_status(
        self, response: httpx.Response, message: str
    ) -> None:
        if response.is_success:
            return
        raise JiraClientError(message, response.status_code, response.text)

    # ----- core REST API methods -----

    def get_issue(self, issue_key: str) -> dict[str, JSONValue]:
        """GET /rest/api/3/issue/{issueKey}"""
        path = f"/rest/api/3/issue/{_encode_path_segment(issue_key)}"
        response = self._client.get(path)
        self._raise_for_status(response, f"Failed to get issue {issue_key}")
        return cast(dict[str, JSONValue], response.json())

    def get_transitions(self, issue_key: str) -> list[dict[str, JSONValue]]:
        """GET /rest/api/3/issue/{issueKey}/transitions"""
        path = f"/rest/api/3/issue/{_encode_path_segment(issue_key)}/transitions"
        response = self._client.get(path)
        self._raise_for_status(
            response, f"Failed to get transitions for {issue_key}"
        )
        payload = cast(dict[str, JSONValue], response.json())
        transitions = payload.get("transitions", [])
        if not isinstance(transitions, list):
            return []
        return cast(list[dict[str, JSONValue]], transitions)

    def transition_issue(self, issue_key: str, transition_id: str) -> None:
        """POST /rest/api/3/issue/{issueKey}/transitions"""
        path = f"/rest/api/3/issue/{_encode_path_segment(issue_key)}/transitions"
        response = self._client.post(
            path, json={"transition": {"id": transition_id}}
        )
        self._raise_for_status(
            response, f"Failed to transition issue {issue_key}"
        )

    def add_comment(self, issue_key: str, body: ADFDocument) -> None:
        """POST /rest/api/3/issue/{issueKey}/comment"""
        path = f"/rest/api/3/issue/{_encode_path_segment(issue_key)}/comment"
        response = self._client.post(path, json={"body": body})
        self._raise_for_status(
            response, f"Failed to add comment to {issue_key}"
        )

    def update_custom_field(
        self, issue_key: str, field_id: str, value: object
    ) -> None:
        """PUT /rest/api/3/issue/{issueKey} (single custom field set)."""
        path = f"/rest/api/3/issue/{_encode_path_segment(issue_key)}"
        response = self._client.put(path, json={"fields": {field_id: value}})
        self._raise_for_status(
            response,
            f"Failed to update custom field {field_id} on {issue_key}",
        )

    def search_issues_by_project(
        self, project_key: str, limit: int = 50
    ) -> list[dict[str, JSONValue]]:
        """POST /rest/api/3/search/jql with project filter."""
        jql = f"project = {project_key} ORDER BY updated DESC"
        response = self._client.post(
            "/rest/api/3/search/jql",
            json={
                "jql": jql,
                "maxResults": limit,
                "fields": [
                    "summary",
                    "description",
                    "status",
                    "issuetype",
                    "project",
                    "assignee",
                ],
            },
        )
        self._raise_for_status(
            response, f"Failed to search issues for project {project_key}"
        )
        payload = cast(dict[str, JSONValue], response.json())
        issues = payload.get("issues", [])
        if not isinstance(issues, list):
            return []
        return cast(list[dict[str, JSONValue]], issues)

    def get_active_sprint_id_for_project(
        self, project_key: str
    ) -> Optional[int]:
        """Return the first active sprint id across any scrum board for the project."""
        boards_response = self._client.get(
            "/rest/agile/1.0/board",
            params={"projectKeyOrId": project_key, "type": "scrum"},
        )
        self._raise_for_status(
            boards_response,
            f"Failed to get scrum boards for project {project_key}",
        )

        boards_payload = cast(dict[str, JSONValue], boards_response.json())
        boards_raw = boards_payload.get("values", [])
        boards = boards_raw if isinstance(boards_raw, list) else []

        for board in boards:
            if not isinstance(board, dict):
                continue
            board_id = board.get("id")
            if board_id is None:
                continue

            sprint_response = self._client.get(
                f"/rest/agile/1.0/board/{_encode_path_segment(str(board_id))}/sprint",
                params={"state": "active", "maxResults": 1},
            )

            if not sprint_response.is_success:
                # Skip boards we can't see; bail on anything else
                if sprint_response.status_code in (403, 404):
                    continue
                self._raise_for_status(
                    sprint_response,
                    f"Failed to get active sprint for board {board_id}",
                )

            sprint_payload = cast(
                dict[str, JSONValue], sprint_response.json()
            )
            sprints_raw = sprint_payload.get("values", [])
            sprints = sprints_raw if isinstance(sprints_raw, list) else []

            for sprint in sprints:
                if not isinstance(sprint, dict):
                    continue
                sprint_id = sprint.get("id")
                if sprint_id is not None:
                    return int(cast(Union[int, str], sprint_id))

        return None

    def add_issues_to_sprint(
        self, sprint_id: int, issue_keys: list[str]
    ) -> None:
        """POST /rest/agile/1.0/sprint/{sprintId}/issue"""
        if not issue_keys:
            return
        path = f"/rest/agile/1.0/sprint/{_encode_path_segment(str(sprint_id))}/issue"
        response = self._client.post(path, json={"issues": issue_keys})
        self._raise_for_status(
            response, f"Failed to add issues to sprint {sprint_id}"
        )

    def add_issues_to_active_sprint(
        self, project_key: str, issue_keys: list[str]
    ) -> bool:
        """Convenience: discover active sprint then add issues. Returns True if a sprint was found."""
        sprint_id = self.get_active_sprint_id_for_project(project_key)
        if sprint_id is None:
            return False
        self.add_issues_to_sprint(sprint_id, issue_keys)
        return True

    def get_issue_types(self) -> list[dict[str, JSONValue]]:
        """GET /rest/api/3/issuetype"""
        response = self._client.get("/rest/api/3/issuetype")
        self._raise_for_status(response, "Failed to get issue types")
        return cast(list[dict[str, JSONValue]], response.json())

    def create_issue(
        self,
        project_key: str,
        summary: str,
        *,
        issue_type_name: str = "Story",
        description: Optional[str] = None,
        labels: Optional[list[str]] = None,
    ) -> dict[str, str]:
        """Create a top-level (non-subtask) issue in `project_key`.

        Not present in jira-client.ts but needed for tests and programmatic
        issue creation. `issue_type_name` is passed by name; Jira resolves it
        against the project's configured types and rejects unknown names with
        400. Returns {'id', 'key'}.
        """
        fields: dict[str, JSONValue] = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type_name},
            "summary": summary,
        }
        if description:
            fields["description"] = cast(
                JSONValue, self.create_text_comment(description)
            )
        if labels:
            fields["labels"] = list(labels)

        response = self._client.post(
            "/rest/api/3/issue", json={"fields": fields}
        )
        self._raise_for_status(
            response, f"Failed to create {issue_type_name} in {project_key}"
        )
        created = cast(dict[str, JSONValue], response.json())
        new_id = created.get("id")
        new_key = created.get("key")
        if not isinstance(new_id, str) or not isinstance(new_key, str):
            raise JiraClientError(
                f"Issue creation response missing id/key: {created}"
            )
        return {"id": new_id, "key": new_key}

    def create_subtask(
        self,
        parent_key: str,
        summary: str,
        description: Optional[str] = None,
        labels: Optional[list[str]] = None,
    ) -> dict[str, str]:
        """Create a sub-task under `parent_key`. Returns {'id', 'key'}.

        1. Fetch parent to get project id
        2. Discover sub-task issue type via /rest/api/3/issuetype
        3. POST /rest/api/3/issue
        """
        parent = self.get_issue(parent_key)
        fields_obj = parent.get("fields")
        if not isinstance(fields_obj, dict):
            raise JiraClientError(
                f"Parent {parent_key} response missing fields"
            )
        project_obj = fields_obj.get("project")
        if not isinstance(project_obj, dict):
            raise JiraClientError(
                f"Parent {parent_key} response missing project info"
            )
        project_id = project_obj.get("id")
        if not isinstance(project_id, str):
            raise JiraClientError(
                f"Parent {parent_key} project id is not a string"
            )

        issue_types = self.get_issue_types()
        subtask_type: Optional[dict[str, JSONValue]] = next(
            (t for t in issue_types if bool(t.get("subtask"))), None
        )
        if subtask_type is None:
            raise JiraClientError(
                "No sub-task issue type found in this Jira instance"
            )
        subtask_type_id = subtask_type.get("id")
        if not isinstance(subtask_type_id, str):
            raise JiraClientError(
                "Sub-task issue type missing id field"
            )

        fields: dict[str, JSONValue] = {
            "project": {"id": project_id},
            "parent": {"key": parent_key},
            "issuetype": {"id": subtask_type_id},
            "summary": summary,
        }
        if description:
            fields["description"] = cast(
                JSONValue, self.create_text_comment(description)
            )
        if labels:
            fields["labels"] = list(labels)

        response = self._client.post(
            "/rest/api/3/issue", json={"fields": fields}
        )
        self._raise_for_status(
            response, f"Failed to create sub-task under {parent_key}"
        )
        created = cast(dict[str, JSONValue], response.json())
        new_id = created.get("id")
        new_key = created.get("key")
        if not isinstance(new_id, str) or not isinstance(new_key, str):
            raise JiraClientError(
                f"Sub-task creation response missing id/key: {created}"
            )
        return {"id": new_id, "key": new_key}

    def delete_issue(
        self, issue_key: str, *, delete_subtasks: bool = False
    ) -> None:
        """DELETE /rest/api/3/issue/{issueKey}.

        Not present in jira-client.ts but needed for tests / cleanup. By
        default Jira refuses to delete an issue that still has sub-tasks;
        pass `delete_subtasks=True` to recursively remove them.
        """
        path = f"/rest/api/3/issue/{_encode_path_segment(issue_key)}"
        params = {"deleteSubtasks": "true"} if delete_subtasks else None
        response = self._client.delete(path, params=params)
        self._raise_for_status(response, f"Failed to delete issue {issue_key}")

    def update_labels(self, issue_key: str, labels: list[str]) -> None:
        """PUT /rest/api/3/issue/{issueKey} (append labels, does not replace)."""
        path = f"/rest/api/3/issue/{_encode_path_segment(issue_key)}"
        response = self._client.put(
            path,
            json={
                "update": {"labels": [{"add": label} for label in labels]}
            },
        )
        self._raise_for_status(
            response, f"Failed to update labels on {issue_key}"
        )

    # ----- ADF helper -----

    @staticmethod
    def create_text_comment(text: str) -> ADFDocument:
        """Build a minimal ADF document containing a single text paragraph."""
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }
