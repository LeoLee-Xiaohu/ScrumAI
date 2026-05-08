import subprocess

from runners.auto_workspace import (
    IN_PROGRESS_STATUSES,
    _ensure_workspace_base_branch_current,
    _ensure_workspace_issue_link,
    _extract_execution_id,
    _format_gh_pr_create_error,
    _gh_subprocess_env,
    _is_execution_failed,
    _is_execution_success,
    _maybe_create_pr_and_review,
    _normalize_status,
    _parse_github_repo,
    _pr_number_from_url,
)
from mcp_adapter import McpIssue


def _completed(args, stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


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


def test_gh_subprocess_env_strips_process_level_token_overrides(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "stale-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "stale-github-token")

    env = _gh_subprocess_env()

    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env


def test_format_gh_pr_create_error_preserves_non_auth_errors():
    message = "pull request create failed: some unrelated problem"
    assert _format_gh_pr_create_error(message) == message


def test_format_gh_pr_create_error_adds_auth_diagnostics(monkeypatch):
    import runners.auto_workspace as auto_workspace

    monkeypatch.setattr(
        auto_workspace,
        "_gh_auth_status_summary",
        lambda: "github.com | Logged in to github.com account LeoLee-Xiaohu | Token scopes: 'repo'",
    )

    message = "pull request create failed: GraphQL: Resource not accessible by personal access token (createPullRequest)"
    formatted = _format_gh_pr_create_error(message)

    assert "stale GH_TOKEN/GITHUB_TOKEN" in formatted
    assert "Token scopes: 'repo'" in formatted
    assert message in formatted


def test_extract_execution_id_from_common_nested_shapes():
    assert _extract_execution_id({"execution_id": "exec-1"}) == "exec-1"
    assert _extract_execution_id({"session": {"execution_process_id": "exec-2"}}) == "exec-2"
    assert _extract_execution_id({"execution_process": {"id": "exec-3"}}) == "exec-3"


def test_workspace_base_branch_check_passes_when_local_matches_remote(tmp_path, monkeypatch):
    import runners.auto_workspace as auto_workspace

    def fake_run_git(args, cwd, check):
        command = tuple(args)
        if command == ("rev-parse", "--show-toplevel"):
            return _completed(args, str(tmp_path))
        if command == ("fetch", "origin", "main"):
            return _completed(args, "")
        if command == ("rev-parse", "refs/heads/main"):
            return _completed(args, "abc123")
        if command == ("rev-parse", "refs/remotes/origin/main"):
            return _completed(args, "abc123")
        raise AssertionError(f"Unexpected git command: {args}")

    monkeypatch.setattr(auto_workspace, "_run_git", fake_run_git)

    ok, message = _ensure_workspace_base_branch_current(
        repo={"name": "NeckFlappy", "path": str(tmp_path)},
        base_branch="main",
        sync=False,
    )

    assert ok is True
    assert message == ""


def test_workspace_base_branch_check_blocks_when_local_is_behind(tmp_path, monkeypatch):
    import runners.auto_workspace as auto_workspace

    def fake_run_git(args, cwd, check):
        command = tuple(args)
        if command == ("rev-parse", "--show-toplevel"):
            return _completed(args, str(tmp_path))
        if command == ("fetch", "origin", "main"):
            return _completed(args, "")
        if command == ("rev-parse", "refs/heads/main"):
            return _completed(args, "local123")
        if command == ("rev-parse", "refs/remotes/origin/main"):
            return _completed(args, "remote123")
        if command == ("merge-base", "--is-ancestor", "local123", "remote123"):
            return _completed(args, "")
        if command == ("merge-base", "--is-ancestor", "remote123", "local123"):
            return _completed(args, "", returncode=1)
        raise AssertionError(f"Unexpected git command: {args}")

    monkeypatch.setattr(auto_workspace, "_run_git", fake_run_git)

    ok, message = _ensure_workspace_base_branch_current(
        repo={"name": "NeckFlappy", "path": str(tmp_path)},
        base_branch="main",
        sync=False,
    )

    assert ok is False
    assert "--sync-base-branch" in message


def test_workspace_base_branch_check_fast_forwards_when_requested(tmp_path, monkeypatch):
    import runners.auto_workspace as auto_workspace

    seen_commands = []

    def fake_run_git(args, cwd, check):
        seen_commands.append(tuple(args))
        command = tuple(args)
        if command == ("rev-parse", "--show-toplevel"):
            return _completed(args, str(tmp_path))
        if command == ("fetch", "origin", "main"):
            return _completed(args, "")
        if command == ("rev-parse", "refs/heads/main"):
            return _completed(args, "local123")
        if command == ("rev-parse", "refs/remotes/origin/main"):
            return _completed(args, "remote123")
        if command == ("merge-base", "--is-ancestor", "local123", "remote123"):
            return _completed(args, "")
        if command == ("merge-base", "--is-ancestor", "remote123", "local123"):
            return _completed(args, "", returncode=1)
        if command == ("checkout", "main"):
            return _completed(args, "")
        if command == ("merge", "--ff-only", "refs/remotes/origin/main"):
            return _completed(args, "Updating local main")
        raise AssertionError(f"Unexpected git command: {args}")

    monkeypatch.setattr(auto_workspace, "_run_git", fake_run_git)

    ok, message = _ensure_workspace_base_branch_current(
        repo={"name": "NeckFlappy", "path": str(tmp_path)},
        base_branch="main",
        sync=True,
    )

    assert ok is True
    assert message == ""
    assert ("checkout", "main") in seen_commands
    assert ("merge", "--ff-only", "refs/remotes/origin/main") in seen_commands


def test_workspace_base_branch_check_blocks_when_local_is_ahead(tmp_path, monkeypatch):
    import runners.auto_workspace as auto_workspace

    def fake_run_git(args, cwd, check):
        command = tuple(args)
        if command == ("rev-parse", "--show-toplevel"):
            return _completed(args, str(tmp_path))
        if command == ("fetch", "origin", "main"):
            return _completed(args, "")
        if command == ("rev-parse", "refs/heads/main"):
            return _completed(args, "local123")
        if command == ("rev-parse", "refs/remotes/origin/main"):
            return _completed(args, "remote123")
        if command == ("merge-base", "--is-ancestor", "local123", "remote123"):
            return _completed(args, "", returncode=1)
        if command == ("merge-base", "--is-ancestor", "remote123", "local123"):
            return _completed(args, "")
        raise AssertionError(f"Unexpected git command: {args}")

    monkeypatch.setattr(auto_workspace, "_run_git", fake_run_git)

    ok, message = _ensure_workspace_base_branch_current(
        repo={"name": "NeckFlappy", "path": str(tmp_path)},
        base_branch="main",
        sync=True,
    )

    assert ok is False
    assert "ahead of origin/main" in message


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

        def get_workspace_http(self, workspace_id, backend_url=None):
            return False, None, ""

        def get_workspace_editor_path_http(self, workspace_id, backend_url=None):
            return False, None, ""

        def get_workspace_repos_http(self, workspace_id, backend_url=None):
            return False, None, ""

        def get_workspace_summary_http(self, workspace_id, archived=False, backend_url=None):
            return False, None, ""

        def get_session_http(self, session_id, backend_url=None):
            return False, None, ""

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

        def get_workspace_http(self, workspace_id, backend_url=None):
            return False, None, ""

        def get_workspace_editor_path_http(self, workspace_id, backend_url=None):
            return False, None, ""

        def get_workspace_repos_http(self, workspace_id, backend_url=None):
            return False, None, ""

        def get_workspace_summary_http(self, workspace_id, archived=False, backend_url=None):
            return False, None, ""

        def get_session_http(self, session_id, backend_url=None):
            return False, None, ""

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


def test_backend_summary_completion_creates_pr_without_execution_id(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self):
            self.updated = []

        def get_workspace_http(self, workspace_id, backend_url=None):
            return True, {"id": workspace_id, "branch": "scrumai/test", "archived": False}, ""

        def get_workspace_editor_path_http(self, workspace_id, backend_url=None):
            return True, str(tmp_path), ""

        def get_workspace_repos_http(self, workspace_id, backend_url=None):
            return True, [{"id": "repo-1", "path": str(tmp_path), "target_branch": "main"}], ""

        def get_workspace_summary_http(self, workspace_id, archived=False, backend_url=None):
            return True, {
                "workspace_id": workspace_id,
                "latest_session_id": "session-1",
                "latest_process_status": "completed",
                "latest_process_completed_at": "2026-05-08T00:00:00+00:00",
            }, ""

        def get_session_http(self, session_id, backend_url=None):
            return True, {"id": session_id, "agent_working_dir": None}, ""

        def list_sessions(self, workspace_id):
            return [{"id": "session-1"}]

        def update_issue(self, issue_id, status):
            self.updated.append((issue_id, status))
            return True

    def fake_create_pull_request(**kwargs):
        assert kwargs["repo_path"] == str(tmp_path)
        assert kwargs["head_branch"] == "scrumai/test"
        return {
            "url": "https://github.com/oldcai/ScrumAI/pull/125",
            "number": 125,
            "head": kwargs["head_branch"],
            "base": kwargs["base_branch"],
        }

    import runners.auto_workspace as auto_workspace

    monkeypatch.setattr(auto_workspace, "create_pull_request", fake_create_pull_request)

    client = FakeClient()
    issue = McpIssue(id="issue-1", simple_id="STORY-1", title="[STORY-1] Test", status="In progress")
    record = {
        "workspace_id": "workspace-1",
        "session_id": "session-1",
        "base_branch": "main",
        "executor": "CODEX",
        "repo_id": "repo-1",
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

    assert record["workspace_branch"] == "scrumai/test"
    assert record["latest_process_status"] == "completed"
    assert record["pr_state"] == "created"
    assert record["issue_status_after_pr"] == "In review"
