"""Unit tests for jira_client.JiraClient.

All tests use httpx.MockTransport to capture outgoing requests and serve canned
responses, so no network access is required. Each method in the client has at
minimum one happy-path test and one error-path test where applicable.
"""

from __future__ import annotations

import base64
import json
from typing import Callable, Optional
from urllib.parse import parse_qs

import httpx
import pytest

from jira_client import JiraClient, JiraClientError


BASE_URL = "https://example.atlassian.net"
EMAIL = "tester@example.com"
TOKEN = "test-token"
EXPECTED_AUTH = "Basic " + base64.b64encode(
    f"{EMAIL}:{TOKEN}".encode("utf-8")
).decode("ascii")


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    base_url: str = BASE_URL,
) -> JiraClient:
    return JiraClient(
        base_url=base_url,
        email=EMAIL,
        api_token=TOKEN,
        transport=httpx.MockTransport(handler),
    )


# ----- construction / auth -----


def test_basic_auth_header_is_set() -> None:
    captured: dict[str, Optional[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={"key": "X-1", "fields": {}})

    with make_client(handler) as client:
        client.get_issue("X-1")

    assert captured["auth"] == EXPECTED_AUTH
    assert captured["accept"] == "application/json"


def test_base_url_trailing_slash_is_stripped() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"key": "X-1", "fields": {}})

    with make_client(handler, base_url=f"{BASE_URL}/") as client:
        client.get_issue("X-1")

    assert captured["url"].startswith(f"{BASE_URL}/rest/api/3/issue/X-1")
    assert "//rest" not in captured["url"][len("https://"):]


def test_construction_requires_base_url_and_credentials() -> None:
    with pytest.raises(ValueError):
        JiraClient(base_url="", email=EMAIL, api_token=TOKEN)
    with pytest.raises(ValueError):
        JiraClient(base_url=BASE_URL, email="", api_token=TOKEN)
    with pytest.raises(ValueError):
        JiraClient(base_url=BASE_URL, email=EMAIL, api_token="")


def test_from_env_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", EMAIL)
    monkeypatch.setenv("JIRA_API_TOKEN", TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"key": "X-1", "fields": {}})

    with JiraClient.from_env(transport=httpx.MockTransport(handler)) as client:
        issue = client.get_issue("X-1")
    assert issue["key"] == "X-1"


def test_from_env_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        JiraClient.from_env()
    assert "JIRA_BASE_URL" in str(excinfo.value)
    assert "JIRA_EMAIL" in str(excinfo.value)
    assert "JIRA_API_TOKEN" in str(excinfo.value)


# ----- get_issue -----


def test_get_issue_happy_path() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "key": "PROJ-123",
                "fields": {"summary": "Hello", "project": {"id": "10001"}},
            },
        )

    with make_client(handler) as client:
        issue = client.get_issue("PROJ-123")

    assert captured["method"] == "GET"
    assert captured["path"] == "/rest/api/3/issue/PROJ-123"
    assert issue["key"] == "PROJ-123"


def test_get_issue_raises_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Issue does not exist")

    with make_client(handler) as client, pytest.raises(JiraClientError) as excinfo:
        client.get_issue("MISSING-1")

    assert excinfo.value.status_code == 404
    assert "MISSING-1" in str(excinfo.value)
    assert excinfo.value.details == "Issue does not exist"


def test_get_issue_url_escapes_special_chars() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # raw_path preserves percent-encoding; .path is already decoded
        captured["raw_path"] = request.url.raw_path.decode("ascii")
        return httpx.Response(200, json={"key": "X", "fields": {}})

    with make_client(handler) as client:
        client.get_issue("WEIRD KEY/with#chars")

    # spaces / slashes / hashes must be percent-encoded so they don't break the route
    assert captured["raw_path"].startswith("/rest/api/3/issue/")
    tail = captured["raw_path"][len("/rest/api/3/issue/"):]
    assert "/" not in tail
    assert " " not in tail
    assert "#" not in tail
    assert "%20" in tail  # space encoded
    assert "%2F" in tail  # slash encoded
    assert "%23" in tail  # hash encoded


# ----- get_transitions -----


def test_get_transitions_returns_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/PROJ-1/transitions"
        return httpx.Response(
            200,
            json={
                "transitions": [
                    {"id": "11", "name": "To Do", "to": {"name": "To Do"}},
                    {"id": "21", "name": "Done", "to": {"name": "Done"}},
                ]
            },
        )

    with make_client(handler) as client:
        transitions = client.get_transitions("PROJ-1")

    assert len(transitions) == 2
    assert transitions[0]["id"] == "11"


def test_get_transitions_empty_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with make_client(handler) as client:
        assert client.get_transitions("PROJ-1") == []


# ----- transition_issue -----


def test_transition_issue_sends_transition_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(204)

    with make_client(handler) as client:
        client.transition_issue("PROJ-1", "21")

    assert captured["method"] == "POST"
    assert captured["path"] == "/rest/api/3/issue/PROJ-1/transitions"
    assert captured["body"] == {"transition": {"id": "21"}}


def test_transition_issue_raises_on_400() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad transition")

    with make_client(handler) as client, pytest.raises(JiraClientError) as excinfo:
        client.transition_issue("PROJ-1", "99")
    assert excinfo.value.status_code == 400


# ----- add_comment -----


def test_add_comment_wraps_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(201, json={"id": "10001"})

    adf = JiraClient.create_text_comment("hi there")
    with make_client(handler) as client:
        client.add_comment("PROJ-1", adf)

    assert captured["body"] == {"body": adf}


# ----- update_custom_field -----


def test_update_custom_field_sends_put_with_fields() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(204)

    with make_client(handler) as client:
        client.update_custom_field("PROJ-1", "customfield_10001", 7.5)

    assert captured["method"] == "PUT"
    assert captured["body"] == {"fields": {"customfield_10001": 7.5}}


# ----- search_issues_by_project -----


def test_search_issues_by_project_builds_jql() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"issues": [{"key": "PROJ-1"}, {"key": "PROJ-2"}]},
        )

    with make_client(handler) as client:
        issues = client.search_issues_by_project("PROJ", limit=25)

    assert captured["path"] == "/rest/api/3/search/jql"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["jql"] == "project = PROJ ORDER BY updated DESC"
    assert body["maxResults"] == 25
    assert "summary" in body["fields"]
    assert len(issues) == 2


def test_search_issues_by_project_handles_missing_issues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with make_client(handler) as client:
        assert client.search_issues_by_project("PROJ") == []


# ----- get_active_sprint_id_for_project -----


def test_get_active_sprint_finds_first_sprint_across_boards() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        calls.append(f"{request.method} {url.path}?{url.query.decode()}")

        if url.path == "/rest/agile/1.0/board":
            params = parse_qs(url.query.decode())
            assert params["projectKeyOrId"] == ["PROJ"]
            assert params["type"] == ["scrum"]
            return httpx.Response(
                200, json={"values": [{"id": 10}, {"id": 11}, {"id": 12}]}
            )

        if url.path == "/rest/agile/1.0/board/10/sprint":
            return httpx.Response(403, text="forbidden")  # skip
        if url.path == "/rest/agile/1.0/board/11/sprint":
            return httpx.Response(200, json={"values": []})  # no active sprints
        if url.path == "/rest/agile/1.0/board/12/sprint":
            return httpx.Response(200, json={"values": [{"id": "555"}]})

        return httpx.Response(404)

    with make_client(handler) as client:
        sprint_id = client.get_active_sprint_id_for_project("PROJ")

    assert sprint_id == 555
    # all three board sprint endpoints should have been polled in order
    assert any("/board/10/sprint" in call for call in calls)
    assert any("/board/11/sprint" in call for call in calls)
    assert any("/board/12/sprint" in call for call in calls)


def test_get_active_sprint_returns_none_when_no_sprints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/agile/1.0/board":
            return httpx.Response(200, json={"values": [{"id": 1}]})
        return httpx.Response(200, json={"values": []})

    with make_client(handler) as client:
        assert client.get_active_sprint_id_for_project("PROJ") is None


def test_get_active_sprint_propagates_unexpected_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/agile/1.0/board":
            return httpx.Response(200, json={"values": [{"id": 1}]})
        return httpx.Response(500, text="boom")

    with make_client(handler) as client, pytest.raises(JiraClientError) as excinfo:
        client.get_active_sprint_id_for_project("PROJ")
    assert excinfo.value.status_code == 500


# ----- add_issues_to_sprint / active sprint -----


def test_add_issues_to_sprint_no_op_when_empty() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(204)

    with make_client(handler) as client:
        client.add_issues_to_sprint(123, [])

    assert calls == []  # no HTTP call made


def test_add_issues_to_sprint_sends_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(204)

    with make_client(handler) as client:
        client.add_issues_to_sprint(42, ["PROJ-1", "PROJ-2"])

    assert captured["path"] == "/rest/agile/1.0/sprint/42/issue"
    assert captured["body"] == {"issues": ["PROJ-1", "PROJ-2"]}


def test_add_issues_to_active_sprint_returns_false_when_no_sprint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/agile/1.0/board":
            return httpx.Response(200, json={"values": []})
        return httpx.Response(200, json={"values": []})

    with make_client(handler) as client:
        assert (
            client.add_issues_to_active_sprint("PROJ", ["PROJ-1"]) is False
        )


def test_add_issues_to_active_sprint_returns_true_when_added() -> None:
    posted: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/agile/1.0/board":
            return httpx.Response(200, json={"values": [{"id": 5}]})
        if request.url.path == "/rest/agile/1.0/board/5/sprint":
            return httpx.Response(200, json={"values": [{"id": 99}]})
        if request.url.path == "/rest/agile/1.0/sprint/99/issue":
            posted["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(204)
        return httpx.Response(404)

    with make_client(handler) as client:
        result = client.add_issues_to_active_sprint("PROJ", ["PROJ-1"])

    assert result is True
    assert posted["body"] == {"issues": ["PROJ-1"]}


# ----- get_issue_types -----


def test_get_issue_types_returns_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "1", "name": "Task", "subtask": False},
                {"id": "5", "name": "Sub-task", "subtask": True},
            ],
        )

    with make_client(handler) as client:
        types = client.get_issue_types()
    assert any(t["subtask"] for t in types)


# ----- create_subtask -----


def test_create_subtask_happy_path() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/api/3/issue/PROJ-1":
            return httpx.Response(
                200,
                json={
                    "key": "PROJ-1",
                    "fields": {"project": {"id": "10001"}},
                },
            )
        if path == "/rest/api/3/issuetype":
            return httpx.Response(
                200,
                json=[
                    {"id": "1", "subtask": False},
                    {"id": "5", "subtask": True},
                ],
            )
        if path == "/rest/api/3/issue":
            captured["create_body"] = json.loads(
                request.content.decode("utf-8")
            )
            return httpx.Response(201, json={"id": "20001", "key": "PROJ-2"})
        return httpx.Response(404)

    with make_client(handler) as client:
        result = client.create_subtask(
            parent_key="PROJ-1",
            summary="child",
            description="some desc",
            labels=["ai", "scrumai"],
        )

    assert result == {"id": "20001", "key": "PROJ-2"}

    body = captured["create_body"]
    assert isinstance(body, dict)
    fields = body["fields"]
    assert fields["project"] == {"id": "10001"}
    assert fields["parent"] == {"key": "PROJ-1"}
    assert fields["issuetype"] == {"id": "5"}
    assert fields["summary"] == "child"
    assert fields["labels"] == ["ai", "scrumai"]
    # description is wrapped as ADF
    desc = fields["description"]
    assert desc["type"] == "doc"
    assert desc["content"][0]["content"][0]["text"] == "some desc"


def test_create_subtask_raises_when_no_subtask_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/api/3/issue/PROJ-1":
            return httpx.Response(
                200,
                json={"fields": {"project": {"id": "10001"}}},
            )
        if path == "/rest/api/3/issuetype":
            return httpx.Response(
                200, json=[{"id": "1", "subtask": False}]
            )
        return httpx.Response(404)

    with make_client(handler) as client, pytest.raises(JiraClientError) as excinfo:
        client.create_subtask("PROJ-1", "child")
    assert "sub-task issue type" in str(excinfo.value).lower()


def test_create_subtask_omits_optional_fields() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/api/3/issue/PROJ-1":
            return httpx.Response(
                200,
                json={"fields": {"project": {"id": "10001"}}},
            )
        if path == "/rest/api/3/issuetype":
            return httpx.Response(200, json=[{"id": "5", "subtask": True}])
        if path == "/rest/api/3/issue":
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(201, json={"id": "20001", "key": "PROJ-2"})
        return httpx.Response(404)

    with make_client(handler) as client:
        client.create_subtask("PROJ-1", "no extras")

    body = captured["body"]
    assert isinstance(body, dict)
    fields = body["fields"]
    assert "description" not in fields
    assert "labels" not in fields


# ----- delete_issue -----


def test_delete_issue_sends_delete() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["query"] = request.url.query.decode()
        return httpx.Response(204)

    with make_client(handler) as client:
        client.delete_issue("PROJ-1")

    assert captured["method"] == "DELETE"
    assert captured["path"] == "/rest/api/3/issue/PROJ-1"
    # default: no deleteSubtasks param
    assert "deleteSubtasks" not in captured["query"]


def test_delete_issue_passes_delete_subtasks_flag() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.query.decode()
        return httpx.Response(204)

    with make_client(handler) as client:
        client.delete_issue("PROJ-1", delete_subtasks=True)

    assert "deleteSubtasks=true" in captured["query"]


def test_delete_issue_raises_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with make_client(handler) as client, pytest.raises(JiraClientError) as excinfo:
        client.delete_issue("PROJ-NOPE")
    assert excinfo.value.status_code == 404


# ----- update_labels -----


def test_update_labels_appends_with_add_directives() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(204)

    with make_client(handler) as client:
        client.update_labels("PROJ-1", ["ai", "ready"])

    assert captured["method"] == "PUT"
    assert captured["body"] == {
        "update": {"labels": [{"add": "ai"}, {"add": "ready"}]}
    }


# ----- ADF helper -----


def test_create_text_comment_returns_well_formed_adf() -> None:
    doc = JiraClient.create_text_comment("hello world")
    assert doc["type"] == "doc"
    assert doc["version"] == 1
    assert len(doc["content"]) == 1
    para = doc["content"][0]
    assert para["type"] == "paragraph"
    assert para["content"][0] == {"type": "text", "text": "hello world"}


# ----- error class -----


def test_jira_client_error_str_includes_status() -> None:
    err = JiraClientError("oops", status_code=429)
    assert "oops" in str(err)
    assert "429" in str(err)


def test_jira_client_error_str_omits_status_when_none() -> None:
    err = JiraClientError("oops")
    assert str(err) == "oops"
