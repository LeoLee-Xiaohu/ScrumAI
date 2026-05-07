from runners.auto_workspace import (
    IN_PROGRESS_STATUSES,
    _ensure_workspace_issue_link,
    _extract_execution_id,
    _is_execution_failed,
    _is_execution_success,
    _maybe_create_pr_and_review,
    _normalize_status,
    _parse_github_repo,
    _pr_number_from_url,
)
from mcp_adapter import McpIssue


def test_parse_github_repo_from_supported_remote_urls():
    assert _parse_github_repo("git@github.com:oldcai/ScrumAI.git") == "oldcai/ScrumAI"
    assert _parse_github_repo("https://github.com/oldcai/ScrumAI.git") == "oldcai/ScrumAI"
    assert _parse_github_repo("ssh://git@github.com/oldcai/ScrumAI.git") == "oldcai/ScrumAI"


def test_parse_github_repo_rejects_non_github_remote():
    assert _parse_github_repo("git@gitlab.com:oldcai/ScrumAI.git") is None
    assert _parse_github_repo("") is None


def test_normalize_status_handles_common_variants():
    assert _normalize_status("In Progress") == "in progress"
    assert _normalize_status("in_progress") == "in progress"
    assert _normalize_status("In   process") == "in process"
    assert _normalize_status("In-review") == "in review"
    assert _normalize_status("In Process") in IN_PROGRESS_STATUSES


def test_execution_status_classification():
    assert _is_execution_success({"status": "completed", "exit_code": 0})
    assert _is_execution_success({"status": "finished", "exit_code": 0})
    assert _is_execution_success({"is_finished": True, "exit_code": 0})
    assert _is_execution_failed({"status": "failed"})
    assert _is_execution_failed({"status": "error"})
    assert _is_execution_failed({"status": "completed", "exit_code": 1})


def test_pr_number_from_url():
    assert _pr_number_from_url("https://github.com/oldcai/ScrumAI/pull/123") == 123
    assert _pr_number_from_url("https://github.com/oldcai/ScrumAI/issues/123") is None


def test_extract_execution_id_from_common_nested_shapes():
    assert _extract_execution_id({"execution_id": "exec-1"}) == "exec-1"
    assert _extract_execution_id({"session": {"execution_process_id": "exec-2"}}) == "exec-2"
    assert _extract_execution_id({"execution_process": {"id": "exec-3"}}) == "exec-3"


def test_conflict_when_linking_workspace_is_treated_as_already_linked(tmp_path):
    class FakeClient:
        def link_workspace_issue(self, workspace_id, issue_id):
            raise RuntimeError("VK API returned error status: 409 Conflict")

    record = {"workspace_id": "workspace-1"}
    mapping = {"issue_to_workspace": {"issue-1": record}}
    issue = McpIssue(id="issue-1", simple_id="STORY-1", title="Test", status="In process")

    assert _ensure_workspace_issue_link(
        client=FakeClient(),
        issue=issue,
        record=record,
        mapping=mapping,
        mapping_path=tmp_path / "mapping.json",
        dry_run=False,
    )
    assert record["issue_linked"] is True
    assert "issue_link_error" not in record


def test_completed_execution_creates_pr_and_moves_issue_to_review(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self):
            self.updated = []

        def get_execution(self, execution_id):
            assert execution_id == "exec-1"
            return {"status": "completed", "exit_code": 0}

        def list_sessions(self, workspace_id):
            assert workspace_id == "workspace-1"
            return [{"id": "session-1", "working_dir": str(tmp_path)}]

        def update_issue(self, issue_id, status):
            self.updated.append((issue_id, status))
            return True

    def fake_create_pull_request(**kwargs):
        assert kwargs["repo_path"] == str(tmp_path)
        assert kwargs["github_repo"] == "oldcai/ScrumAI"
        assert kwargs["head_branch"] == "scrumai/test"
        assert kwargs["base_branch"] == "main"
        return {
            "url": "https://github.com/oldcai/ScrumAI/pull/123",
            "number": 123,
            "head": kwargs["head_branch"],
            "base": kwargs["base_branch"],
        }

    import runners.auto_workspace as auto_workspace

    monkeypatch.setattr(auto_workspace, "create_pull_request", fake_create_pull_request)

    client = FakeClient()
    issue = McpIssue(id="issue-1", simple_id="STORY-1", title="[STORY-1] Test", status="In process")
    record = {
        "workspace_id": "workspace-1",
        "session_id": "session-1",
        "execution_id": "exec-1",
        "workspace_branch": "scrumai/test",
        "base_branch": "main",
        "executor": "CODEX",
    }
    mapping = {"issue_to_workspace": {"issue-1": record}}

    _maybe_create_pr_and_review(
        client=client,
        issue=issue,
        current_status=_normalize_status(issue.status),
        record=record,
        repo={"id": "repo-1"},
        github_repo="oldcai/ScrumAI",
        pr_base="main",
        review_status="In review",
        pr_draft=False,
        mapping=mapping,
        mapping_path=tmp_path / "mapping.json",
        dry_run=False,
    )

    assert record["pr_state"] == "created"
    assert record["pull_request"]["url"] == "https://github.com/oldcai/ScrumAI/pull/123"
    assert record["issue_status_after_pr"] == "In review"
    assert client.updated == [("issue-1", "In review")]


def test_missing_execution_id_is_recovered_from_latest_session(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self):
            self.updated = []

        def get_execution(self, execution_id):
            assert execution_id == "exec-recovered"
            return {"status": "completed", "exit_code": 0}

        def list_sessions(self, workspace_id):
            assert workspace_id == "workspace-1"
            return [
                {
                    "id": "session-1",
                    "working_dir": str(tmp_path),
                    "execution_process": {"id": "exec-recovered"},
                }
            ]

        def update_issue(self, issue_id, status):
            self.updated.append((issue_id, status))
            return True

    def fake_create_pull_request(**kwargs):
        return {
            "url": "https://github.com/oldcai/ScrumAI/pull/124",
            "number": 124,
            "head": kwargs["head_branch"],
            "base": kwargs["base_branch"],
        }

    import runners.auto_workspace as auto_workspace

    monkeypatch.setattr(auto_workspace, "create_pull_request", fake_create_pull_request)

    client = FakeClient()
    issue = McpIssue(id="issue-1", simple_id="STORY-1", title="[STORY-1] Test", status="In process")
    record = {
        "workspace_id": "workspace-1",
        "session_id": "session-1",
        "workspace_branch": "scrumai/test",
        "base_branch": "main",
        "executor": "CODEX",
    }
    mapping = {"issue_to_workspace": {"issue-1": record}}

    _maybe_create_pr_and_review(
        client=client,
        issue=issue,
        current_status=_normalize_status(issue.status),
        record=record,
        repo={"id": "repo-1"},
        github_repo="oldcai/ScrumAI",
        pr_base="main",
        review_status="In review",
        pr_draft=False,
        mapping=mapping,
        mapping_path=tmp_path / "mapping.json",
        dry_run=False,
    )

    assert record["execution_id"] == "exec-recovered"
    assert record["pr_state"] == "created"
    assert record["issue_status_after_pr"] == "In review"
