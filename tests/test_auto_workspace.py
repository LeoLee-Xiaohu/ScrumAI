from runners.auto_workspace import (
    _is_execution_failed,
    _is_execution_success,
    _normalize_status,
    _parse_github_repo,
    _pr_number_from_url,
)


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
    assert _normalize_status("In-review") == "in review"


def test_execution_status_classification():
    assert _is_execution_success({"status": "completed", "exit_code": 0})
    assert _is_execution_success({"is_finished": True, "exit_code": 0})
    assert _is_execution_failed({"status": "failed"})
    assert _is_execution_failed({"status": "completed", "exit_code": 1})


def test_pr_number_from_url():
    assert _pr_number_from_url("https://github.com/oldcai/ScrumAI/pull/123") == 123
    assert _pr_number_from_url("https://github.com/oldcai/ScrumAI/issues/123") is None
