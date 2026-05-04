"""Auto-create Vibe Kanban workspaces and GitHub PRs from issue state changes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_adapter import (
    McpClient,
    McpIssue,
    SCRUMAI_DESCRIPTION_MARKERS,
    _looks_like_scrumai_issue_title,
    _resolve_project_by_name_or_id,
)


IN_PROGRESS_STATUSES = {"in progress", "in_progress"}
IN_REVIEW_STATUSES = {"in review", "in_review"}
FAILED_EXECUTION_STATUSES = {"failed", "killed", "cancelled", "canceled", "timeout", "timed_out"}
COMPLETED_EXECUTION_STATUSES = {"completed", "complete", "succeeded", "success"}


def run_auto_workspace(args) -> bool:
    mapping_path = Path(getattr(args, "mapping", "kanban_workspace_mapping.json"))
    interval = max(1, int(getattr(args, "interval", 5) or 5))
    run_once = bool(getattr(args, "once", False))
    dry_run = bool(getattr(args, "dry_run", False))
    skip_pr = bool(getattr(args, "skip_pr", False))
    include_existing = bool(getattr(args, "include_existing_in_progress", False))
    executor = (getattr(args, "executor", "CODEX") or "CODEX").upper()
    base_branch = getattr(args, "base_branch", "main") or "main"
    pr_base = getattr(args, "pr_base", None) or base_branch
    review_status = getattr(args, "review_status", "In review") or "In review"

    if not skip_pr and not dry_run and not _check_gh_available():
        return False

    mapping = _load_mapping(mapping_path)

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()

    try:
        print("Fetching organizations and project...")
        org, project, projects = _resolve_project_by_name_or_id(
            client,
            project_name=getattr(args, "project_name", None),
            project_id=getattr(args, "project_id", None),
        )
        if not org:
            print("Error: No organizations found.")
            print("Please make sure you are signed in to Vibe Kanban.")
            return False
        if not project:
            project_ref = getattr(args, "project_id", None) or getattr(args, "project_name", None)
            print(f"Project '{project_ref}' not found.")
            print("Available projects:")
            for candidate in projects:
                print(f"  - id={candidate.id} name={candidate.name}")
            return False

        repo = _resolve_repo(client, getattr(args, "repo_id", None), getattr(args, "repo_name", None))
        if not repo:
            return False

        github_repo = getattr(args, "github_repo", None) or _infer_github_repo(repo)
        if not skip_pr and not github_repo:
            print("Error: Could not infer GitHub repo. Pass --github-repo owner/repo.")
            return False

        _ensure_mapping_header(
            mapping=mapping,
            project_id=project.id,
            project_name=project.name,
            organization_id=org.id,
            repo=repo,
            executor=executor,
            base_branch=base_branch,
            github_repo=github_repo,
            pr_base=pr_base,
        )
        _save_mapping(mapping_path, mapping)

        print(f"Watching project '{project.name}' for issues entering In progress")
        print(f"Workspace repo: {_repo_label(repo)}")
        print(f"Executor: {executor}; base branch: {base_branch}")
        if skip_pr:
            print("PR automation: disabled via --skip-pr")
        else:
            print(f"PR target: {github_repo}; review status: {review_status}")
        print(f"Mode: {'dry-run, ' if dry_run else ''}{'single scan' if run_once else f'continuous (every {interval}s)'}")
        print("Press Ctrl+C to stop.\n")

        previous_status_by_issue_id: dict[str, str] = {}
        first_tick = True

        while True:
            issues = client.list_all_issues(project.id)
            issue_by_id = {issue.id: issue for issue in issues}

            for issue in issues:
                current_status = _normalize_status(issue.status)
                previous_status = previous_status_by_issue_id.get(issue.id)
                record = _issue_records(mapping).get(issue.id)

                should_start_workspace = (
                    current_status in IN_PROGRESS_STATUSES
                    and previous_status not in IN_PROGRESS_STATUSES
                    and not _record_has_workspace(record)
                    and _is_scrumai_issue(client, issue)
                )
                if first_tick and not include_existing and current_status in IN_PROGRESS_STATUSES:
                    should_start_workspace = False

                if should_start_workspace:
                    if not _start_issue_workspace(
                        client=client,
                        issue=issue,
                        repo=repo,
                        executor=executor,
                        base_branch=base_branch,
                        mapping=mapping,
                        mapping_path=mapping_path,
                        dry_run=dry_run,
                    ):
                        return False

                if not skip_pr:
                    record = _issue_records(mapping).get(issue.id)
                    _maybe_create_pr_and_review(
                        client=client,
                        issue=issue,
                        current_status=current_status,
                        record=record,
                        repo=repo,
                        github_repo=github_repo,
                        pr_base=pr_base,
                        review_status=review_status,
                        pr_draft=bool(getattr(args, "pr_draft", False)),
                        mapping=mapping,
                        mapping_path=mapping_path,
                        dry_run=dry_run,
                    )

                previous_status_by_issue_id[issue.id] = current_status

            _retry_review_status_for_existing_prs(
                client=client,
                issue_by_id=issue_by_id,
                mapping=mapping,
                mapping_path=mapping_path,
                review_status=review_status,
                dry_run=dry_run,
            )

            if run_once:
                break
            first_tick = False
            time.sleep(interval)

        return True
    except KeyboardInterrupt:
        print("\nAuto workspace watcher stopped by user.")
        return True
    except Exception as e:
        print(f"Auto workspace error: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        client.close()


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"issue_to_workspace": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Mapping file {path} must contain a JSON object.")
    data.setdefault("issue_to_workspace", {})
    return data


def _save_mapping(path: Path, mapping: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)
        f.write("\n")
    temp_path.replace(path)


def _issue_records(mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = mapping.setdefault("issue_to_workspace", {})
    if not isinstance(records, dict):
        raise ValueError("mapping.issue_to_workspace must be an object.")
    return records


def _ensure_mapping_header(
    mapping: dict[str, Any],
    project_id: str,
    project_name: str,
    organization_id: str,
    repo: dict[str, Any],
    executor: str,
    base_branch: str,
    github_repo: str | None,
    pr_base: str,
) -> None:
    mapping["project_id"] = project_id
    mapping["project_name"] = project_name
    mapping["organization_id"] = organization_id
    mapping["repo_id"] = repo.get("id")
    mapping["repo_name"] = repo.get("name") or Path(str(repo.get("path", ""))).name
    mapping["repo_path"] = repo.get("path")
    mapping["executor"] = executor
    mapping["base_branch"] = base_branch
    if github_repo:
        owner, name = github_repo.split("/", 1)
        mapping["github"] = {
            "owner": owner,
            "repo": name,
            "base": pr_base,
            "full_name": github_repo,
        }
    mapping.setdefault("issue_to_workspace", {})


def _resolve_repo(client: McpClient, repo_id: str | None, repo_name: str | None) -> dict[str, Any] | None:
    if repo_id:
        repo = client.get_repo(repo_id)
        if repo:
            return repo
        print(f"Error: repo id '{repo_id}' was not found.")
        return None

    if not repo_name:
        print("Error: provide --repo-name or --repo-id so the workspace can target the correct repo.")
        return None

    try:
        repos = client.list_repos()
    except Exception as e:
        print(f"Error: Could not list Vibe Kanban repos: {e}")
        print("Make sure Vibe Kanban is running and the target git repo is registered in the UI.")
        return None

    wanted = repo_name.lower()

    def basename(repo: dict[str, Any]) -> str:
        return Path(str(repo.get("path", ""))).name.lower()

    exact = [r for r in repos if str(r.get("name", "")).lower() == wanted]
    if len(exact) == 1:
        return exact[0]

    base_matches = [r for r in repos if basename(r) == wanted]
    if len(base_matches) == 1:
        return base_matches[0]

    path_matches = [r for r in repos if wanted in str(r.get("path", "")).lower()]
    if len(path_matches) == 1:
        return path_matches[0]

    matches = exact or base_matches or path_matches
    if len(matches) > 1:
        print(f"Error: repo name '{repo_name}' matched multiple Vibe Kanban repos.")
        for repo in matches:
            print(f"  - id={repo.get('id')} name={repo.get('name')} path={repo.get('path')}")
        print("Re-run with --repo-id <uuid> to disambiguate.")
        return None

    print(f"Error: repo '{repo_name}' was not found in Vibe Kanban.")
    print("Available repos:")
    for repo in repos:
        print(f"  - id={repo.get('id')} name={repo.get('name')} path={repo.get('path')}")
    return None


def _repo_label(repo: dict[str, Any]) -> str:
    name = repo.get("name") or "<unnamed>"
    path = repo.get("path") or "<unknown path>"
    return f"{name} ({path}, id={repo.get('id')})"


def _infer_github_repo(repo: dict[str, Any]) -> str | None:
    path = repo.get("path")
    if not path:
        return None
    origin_url = _run_git(["config", "--get", "remote.origin.url"], cwd=path, check=False).stdout.strip()
    return _parse_github_repo(origin_url)


def _parse_github_repo(remote_url: str) -> str | None:
    patterns = [
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, remote_url.strip())
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return None


def _normalize_status(status: str | None) -> str:
    return (status or "").strip().lower().replace("-", " ").replace("_", " ")


def _is_scrumai_issue(client: McpClient, issue: McpIssue) -> bool:
    details = client.get_issue(issue.id)
    description = details.get("description") if isinstance(details, dict) else None
    if isinstance(description, str) and all(marker in description for marker in SCRUMAI_DESCRIPTION_MARKERS):
        return True
    return _looks_like_scrumai_issue_title(issue.title)


def _record_has_workspace(record: dict[str, Any] | None) -> bool:
    return bool(record and record.get("workspace_id") and record.get("state") != "failed")


def _start_issue_workspace(
    client: McpClient,
    issue: McpIssue,
    repo: dict[str, Any],
    executor: str,
    base_branch: str,
    mapping: dict[str, Any],
    mapping_path: Path,
    dry_run: bool,
) -> bool:
    records = _issue_records(mapping)
    now = _utc_now()
    records[issue.id] = {
        "state": "creating",
        "issue_id": issue.id,
        "issue_simple_id": issue.simple_id,
        "issue_title": issue.title,
        "repo_id": repo.get("id"),
        "base_branch": base_branch,
        "started_at": now,
    }
    _save_mapping(mapping_path, mapping)

    details = client.get_issue(issue.id)
    prompt = _build_workspace_prompt(issue, details)
    print(f"[workspace] {issue.title}: In progress detected")

    if dry_run:
        print(f"[dry-run] would start workspace with executor={executor}, repo_id={repo.get('id')}, branch={base_branch}")
        records[issue.id].update({
            "state": "dry_run",
            "dry_run_at": _utc_now(),
        })
        _save_mapping(mapping_path, mapping)
        return True

    try:
        result = client.start_workspace(
            name=issue.title,
            prompt=prompt,
            executor=executor,
            repo_id=str(repo.get("id")),
            branch=base_branch,
            issue_id=issue.id,
        )
    except Exception as e:
        records[issue.id].update({
            "state": "failed",
            "error": str(e),
            "failed_at": _utc_now(),
        })
        _save_mapping(mapping_path, mapping)
        print(f"[error] failed to start workspace for {issue.title}: {e}")
        return False

    workspace = result.get("workspace") if isinstance(result.get("workspace"), dict) else {}
    execution = (
        result.get("execution_process")
        if isinstance(result.get("execution_process"), dict)
        else result.get("execution")
        if isinstance(result.get("execution"), dict)
        else {}
    )
    workspace_id = _first_value(result, "workspace_id") or workspace.get("id")
    execution_id = _first_value(result, "execution_id") or execution.get("id")
    branch = workspace.get("branch") or _first_value(result, "workspace_branch")

    session_id = _first_value(result, "session_id")
    if workspace_id and not session_id:
        session_id = _latest_session_id(client, workspace_id)

    records[issue.id].update({
        "state": "created",
        "workspace_id": workspace_id,
        "session_id": session_id,
        "execution_id": execution_id,
        "workspace_branch": branch,
        "created_at": _utc_now(),
        "start_workspace_response": result,
    })
    _save_mapping(mapping_path, mapping)
    print(f"[workspace] created for {issue.title}: workspace_id={workspace_id}, execution_id={execution_id}")
    return True


def _build_workspace_prompt(issue: McpIssue, details: dict[str, Any]) -> str:
    description = details.get("description") or ""
    return "\n".join([
        "You are working on this Vibe Kanban issue.",
        "",
        "Issue:",
        issue.title,
        "",
        "Description:",
        description,
        "",
        "Requirements:",
        "- Implement the ticket in the linked repository.",
        "- Use the existing code style and tests.",
        "- Keep changes scoped to this ticket.",
        "- Run relevant checks if available.",
        "- Do not merge or delete branches.",
    ])


def _latest_session_id(client: McpClient, workspace_id: str) -> str | None:
    try:
        sessions = client.list_sessions(workspace_id)
    except Exception:
        return None
    if not sessions:
        return None
    return str(sessions[-1].get("id") or "") or None


def _maybe_create_pr_and_review(
    client: McpClient,
    issue: McpIssue,
    current_status: str,
    record: dict[str, Any] | None,
    repo: dict[str, Any],
    github_repo: str | None,
    pr_base: str,
    review_status: str,
    pr_draft: bool,
    mapping: dict[str, Any],
    mapping_path: Path,
    dry_run: bool,
) -> None:
    if current_status not in IN_PROGRESS_STATUSES:
        return
    if not record or not record.get("execution_id"):
        return
    if record.get("pull_request", {}).get("url"):
        return
    if record.get("pr_state") in {"no_changes", "failed"}:
        return

    execution = client.get_execution(str(record["execution_id"]))
    if not execution:
        return
    if _is_execution_failed(execution):
        record["execution"] = {
            "state": "failed",
            "message": execution.get("message") or execution.get("final_message") or execution.get("status"),
            "raw": execution,
            "updated_at": _utc_now(),
        }
        _save_mapping(mapping_path, mapping)
        print(f"[execution] failed for {issue.title}; leaving issue in In progress")
        return
    if not _is_execution_success(execution):
        return

    print(f"[execution] completed for {issue.title}; preparing PR")
    record["execution"] = {
        "state": "completed",
        "raw": execution,
        "updated_at": _utc_now(),
    }

    if dry_run:
        print(f"[dry-run] would create PR for {issue.title} and move issue to {review_status}")
        record["pr_state"] = "dry_run"
        _save_mapping(mapping_path, mapping)
        return

    repo_path = _resolve_workspace_repo_path(client, record, repo)
    if not repo_path:
        record["pr_state"] = "failed"
        record["pr_error"] = "Could not resolve workspace repo path."
        _save_mapping(mapping_path, mapping)
        print(f"[error] could not resolve workspace repo path for {issue.title}")
        return

    current_branch = _current_branch(repo_path)
    branch = record.get("workspace_branch")
    if not branch or branch == record.get("base_branch"):
        branch = current_branch
    if not branch:
        branch = _slug_branch(issue)

    try:
        pr = create_pull_request(
            repo_path=repo_path,
            github_repo=str(github_repo),
            head_branch=str(branch),
            base_branch=pr_base,
            title=issue.title,
            body=_build_pr_body(issue, record),
            draft=pr_draft,
        )
    except NoChangesError:
        record["pr_state"] = "no_changes"
        record["pr_error"] = "No code changes detected."
        _save_mapping(mapping_path, mapping)
        print(f"[pr] no changes detected for {issue.title}; leaving issue in In progress")
        return
    except Exception as e:
        record["pr_state"] = "failed"
        record["pr_error"] = str(e)
        _save_mapping(mapping_path, mapping)
        print(f"[error] failed to create PR for {issue.title}: {e}")
        return

    record["pull_request"] = pr
    record["pr_state"] = "created"
    record["pr_created_at"] = _utc_now()
    _save_mapping(mapping_path, mapping)
    print(f"[pr] created for {issue.title}: {pr.get('url')}")

    if client.update_issue(issue_id=issue.id, status=review_status):
        record["issue_status_after_pr"] = review_status
        record["issue_status_updated_at"] = _utc_now()
        _save_mapping(mapping_path, mapping)
        print(f"[review] moved {issue.title} -> {review_status}")
    else:
        print(f"[warn] PR created but failed to move {issue.title} to {review_status}")


def _retry_review_status_for_existing_prs(
    client: McpClient,
    issue_by_id: dict[str, McpIssue],
    mapping: dict[str, Any],
    mapping_path: Path,
    review_status: str,
    dry_run: bool,
) -> None:
    changed = False
    for issue_id, record in _issue_records(mapping).items():
        if not record.get("pull_request", {}).get("url"):
            continue
        if record.get("issue_status_after_pr") == review_status:
            continue
        issue = issue_by_id.get(issue_id)
        if issue and _normalize_status(issue.status) in IN_REVIEW_STATUSES:
            record["issue_status_after_pr"] = review_status
            record["issue_status_updated_at"] = _utc_now()
            changed = True
            continue
        if dry_run:
            print(f"[dry-run] would retry moving issue {issue_id} to {review_status}")
            continue
        if client.update_issue(issue_id=issue_id, status=review_status):
            record["issue_status_after_pr"] = review_status
            record["issue_status_updated_at"] = _utc_now()
            changed = True
            print(f"[review] moved issue {issue_id} -> {review_status}")
    if changed:
        _save_mapping(mapping_path, mapping)


def _resolve_workspace_repo_path(client: McpClient, record: dict[str, Any], repo: dict[str, Any]) -> str | None:
    for candidate in [
        record.get("repo_path"),
        record.get("agent_working_dir"),
        record.get("working_dir"),
    ]:
        if candidate and Path(str(candidate)).exists():
            return str(candidate)

    workspace_id = record.get("workspace_id")
    session_id = record.get("session_id")
    if workspace_id:
        try:
            sessions = client.list_sessions(str(workspace_id))
            for session in sessions:
                if session_id and session.get("id") != session_id:
                    continue
                for key in ("agent_working_dir", "working_dir", "path"):
                    candidate = session.get(key)
                    if candidate and Path(str(candidate)).exists():
                        record[key] = candidate
                        return str(candidate)
        except Exception:
            pass

    return None


class NoChangesError(RuntimeError):
    pass


def create_pull_request(
    repo_path: str,
    github_repo: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    draft: bool = False,
) -> dict[str, Any]:
    repo_dir = Path(repo_path)
    if not repo_dir.exists():
        raise RuntimeError(f"Workspace repo path does not exist: {repo_path}")

    _ensure_gh_auth()
    _ensure_git_repo(repo_dir)
    _ensure_branch(repo_dir, head_branch)
    _commit_dirty_changes(repo_dir, title)

    if not _branch_has_diff(repo_dir, base_branch):
        raise NoChangesError("No branch diff found against base branch.")

    _run_git(["push", "-u", "origin", head_branch], cwd=str(repo_dir), check=True)

    cmd = [
        "gh",
        "pr",
        "create",
        "--repo",
        github_repo,
        "--base",
        base_branch,
        "--head",
        head_branch,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        cmd.append("--draft")

    result = subprocess.run(cmd, cwd=str(repo_dir), text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())

    url = result.stdout.strip().splitlines()[-1].strip()
    number = _pr_number_from_url(url)
    return {
        "url": url,
        "number": number,
        "head": head_branch,
        "base": base_branch,
        "github_repo": github_repo,
        "created_at": _utc_now(),
    }


def _ensure_gh_auth() -> None:
    if not _check_gh_available():
        raise RuntimeError("GitHub CLI is not installed or not authenticated.")


def _check_gh_available() -> bool:
    if not shutil.which("gh"):
        print("Error: GitHub CLI 'gh' is not installed. Install gh or run with --skip-pr.")
        return False
    result = subprocess.run(["gh", "auth", "status"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        print("Error: GitHub CLI is not authenticated. Run: gh auth login")
        if result.stderr.strip():
            print(result.stderr.strip())
        return False
    return True


def _ensure_git_repo(repo_dir: Path) -> None:
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=str(repo_dir), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Not a git repository: {repo_dir}")


def _ensure_branch(repo_dir: Path, branch: str) -> None:
    current = _current_branch(str(repo_dir))
    if current == branch:
        return
    result = _run_git(["checkout", branch], cwd=str(repo_dir), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Could not checkout branch {branch}: {result.stderr.strip()}")


def _current_branch(repo_path: str) -> str | None:
    result = _run_git(["branch", "--show-current"], cwd=repo_path, check=False)
    branch = result.stdout.strip()
    return branch or None


def _commit_dirty_changes(repo_dir: Path, title: str) -> None:
    status = _run_git(["status", "--porcelain"], cwd=str(repo_dir), check=True).stdout.strip()
    if not status:
        return
    _run_git(["add", "-A"], cwd=str(repo_dir), check=True)
    message = f"Implement {title}"
    _run_git(["commit", "-m", message], cwd=str(repo_dir), check=True)


def _branch_has_diff(repo_dir: Path, base_branch: str) -> bool:
    merge_base_ref = f"origin/{base_branch}"
    _run_git(["fetch", "origin", base_branch], cwd=str(repo_dir), check=False)
    result = _run_git(["diff", "--quiet", f"{merge_base_ref}...HEAD"], cwd=str(repo_dir), check=False)
    if result.returncode == 1:
        return True
    if result.returncode == 0:
        return False
    fallback = _run_git(["diff", "--quiet", f"{base_branch}...HEAD"], cwd=str(repo_dir), check=False)
    return fallback.returncode == 1


def _run_git(args: list[str], cwd: str, check: bool) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def _build_pr_body(issue: McpIssue, record: dict[str, Any]) -> str:
    return "\n".join([
        "## Summary",
        "",
        "Automated PR generated from Vibe Kanban issue.",
        "",
        "## Source Issue",
        "",
        f"- Vibe Kanban issue: {issue.simple_id or issue.id}",
        f"- Workspace: {record.get('workspace_id')}",
        f"- Executor: {record.get('executor') or record.get('start_workspace_response', {}).get('executor') or 'CODEX'}",
        f"- Base branch: {record.get('base_branch')}",
        "",
        "## Ticket",
        "",
        issue.title,
        "",
        "## Review Notes",
        "",
        "- Review generated changes before merge.",
        "- This PR was created automatically after the agent execution completed successfully.",
    ])


def _is_execution_success(execution: dict[str, Any]) -> bool:
    status = str(execution.get("status", "")).lower()
    exit_code = execution.get("exit_code")
    is_finished = execution.get("is_finished")
    if status in COMPLETED_EXECUTION_STATUSES and exit_code in (None, 0):
        return True
    return bool(is_finished is True and exit_code in (None, 0) and status not in FAILED_EXECUTION_STATUSES)


def _is_execution_failed(execution: dict[str, Any]) -> bool:
    status = str(execution.get("status", "")).lower()
    exit_code = execution.get("exit_code")
    if status in FAILED_EXECUTION_STATUSES:
        return True
    return exit_code not in (None, 0)


def _first_value(data: dict[str, Any], key: str) -> Any:
    if key in data:
        return data[key]
    for value in data.values():
        if isinstance(value, dict) and key in value:
            return value[key]
    return None


def _slug_branch(issue: McpIssue) -> str:
    raw = issue.simple_id or issue.title
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-").lower()
    return f"scrumai/{slug or issue.id}"


def _pr_number_from_url(url: str) -> int | None:
    match = re.search(r"/pull/(\d+)", url)
    if not match:
        return None
    return int(match.group(1))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    print("Use `uv run python main.py auto-workspace ...`.", file=sys.stderr)
