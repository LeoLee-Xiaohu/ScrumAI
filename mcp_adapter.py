import json
import subprocess
import threading
import queue
import time
import logging
import os
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MCP_SERVER_CMD = ["npx", "-y", "vibe-kanban@0.1.43", "--mcp"]
# Generous default timeout — the MCP server can route through an SSH tunnel
# and `list_issues` paginates over 100s of tasks; 60s was too tight in
# practice and caused spurious timeouts mid-sync.
MCP_CALL_TIMEOUT_SECONDS = 120
MCP_STARTUP_TIMEOUT_SECONDS = 120
MCP_RESPONSE_POLL_INTERVAL_SECONDS = 1
VIBE_BACKEND_DEFAULT_URL = "http://127.0.0.1:63861"
VIBE_BACKEND_URL = os.environ.get("VIBE_BACKEND_URL", VIBE_BACKEND_DEFAULT_URL)

@dataclass
class McpOrganization:
    id: str
    name: str
    slug: str = ""
    is_personal: bool = False

@dataclass
class McpProject:
    id: str
    name: str
    created_at: str
    updated_at: str

@dataclass
class McpIssue:
    id: str
    simple_id: str
    title: str
    status: str
    priority: Optional[str] = None


SCRUMAI_DESCRIPTION_MARKERS = (
    "**Task ID:**",
    "**Dispatched Role:**",
    "**Owner Type:**",
    "**Autonomy Level:**",
    "**Task Description:**",
)


def _format_score_line(scoring: dict) -> str:
    labels = [
        ("complexity", "Complexity"),
        ("risk", "Risk"),
        ("human_judgment", "Human Judgment"),
        ("domain_specificity", "Domain Specificity"),
    ]
    parts = []
    for key, label in labels:
        score = scoring.get(key, {}).get("score")
        if score is not None:
            parts.append(f"{label}: {score}/2")
    return " | ".join(parts)


class McpClient:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        self.lock = threading.Lock()
        self._tools: list[dict] = []
        self._response_queues: dict[int, queue.Queue] = {}
        self._stderr_lines: deque[str] = deque(maxlen=100)
        self._initialize_server()

    def _initialize_server(self):
        env = {
            **os.environ,
            "MCP_HOST": "127.0.0.1",
        }
        try:
            self.process = subprocess.Popen(
                MCP_SERVER_CMD,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            raise RuntimeError("npx not found. Please install Node.js and npm.")

        if self.process.poll() is not None:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"MCP server failed to start: {stderr}")

        self._start_reader()
        self._start_stderr_reader()

        try:
            self._initialize(timeout_seconds=MCP_STARTUP_TIMEOUT_SECONDS)
        except Exception as e:
            raise RuntimeError(
                f"MCP initialize failed: {e}. Stderr: {self._format_stderr_output()}"
            )

    def _start_reader(self):
        def reader():
            while self.process and self.process.poll() is None:
                try:
                    line = self.process.stdout.readline()
                    if line:
                        try:
                            msg = json.loads(line)
                            req_id = msg.get("id")
                            if req_id and req_id in self._response_queues:
                                self._response_queues[req_id].put(msg)
                        except json.JSONDecodeError:
                            pass
                except:
                    break

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

    def _start_stderr_reader(self):
        def reader():
            while self.process and self.process.poll() is None:
                try:
                    line = self.process.stderr.readline()
                    if line:
                        self._stderr_lines.append(line.rstrip())
                except Exception:
                    break

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

    def _format_stderr_output(self) -> str:
        if not self._stderr_lines:
            return "no stderr output"
        return "\n".join(self._stderr_lines)

    def _send_jsonrpc(
        self,
        method: str,
        params: dict = None,
        expect_response: bool = True,
        timeout_seconds: float = MCP_CALL_TIMEOUT_SECONDS,
    ) -> dict:
        with self.lock:
            if not self.process or not self.process.stdin:
                raise RuntimeError("MCP process is not available.")

            if not expect_response:
                # JSON-RPC 2.0: notifications MUST NOT include an id field.
                notification = {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params or {}
                }
                request_str = json.dumps(notification) + "\n"
                self.process.stdin.write(request_str)
                self.process.stdin.flush()
                return {}

            self.request_id += 1
            req_id = self.request_id

            request = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {}
            }

            response_queue = queue.Queue()
            self._response_queues[req_id] = response_queue

            try:
                request_str = json.dumps(request) + "\n"
                self.process.stdin.write(request_str)
                self.process.stdin.flush()

                deadline = time.monotonic() + timeout_seconds
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            f"Timed out waiting for MCP response to '{method}' after {timeout_seconds:.0f}s"
                        )

                    try:
                        msg = response_queue.get(
                            timeout=min(MCP_RESPONSE_POLL_INTERVAL_SECONDS, remaining)
                        )
                        break
                    except queue.Empty:
                        if self.process.poll() is not None:
                            raise RuntimeError(
                                f"MCP server exited while waiting for '{method}'. "
                                f"Stderr: {self._format_stderr_output()}"
                            )

                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")

                return msg.get("result", {})
            finally:
                self._response_queues.pop(req_id, None)

    def _initialize(self, timeout_seconds: float = MCP_CALL_TIMEOUT_SECONDS):
        self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "scrumai",
                "version": "1.0.0"
            }
        }, timeout_seconds=timeout_seconds)

        self._send_jsonrpc("notifications/initialized", {}, expect_response=False)

        tools_result = self._send_jsonrpc("tools/list", {})
        self._tools = tools_result.get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict = None) -> dict:
        result = self._send_jsonrpc("tools/call", {
            "name": tool_name,
            "arguments": arguments or {}
        })
        return result

    def _parse_tool_json(self, result: dict) -> dict:
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            data = json.loads(content[0]["text"])
            if isinstance(data, dict):
                return data
        return {}

    def list_organizations(self) -> list[McpOrganization]:
        try:
            result = self.call_tool("list_organizations", {})
            data = self._parse_tool_json(result)
            return [McpOrganization(**org) for org in data.get("organizations", [])]
        except Exception as e:
            logger.warning(f"Failed to list organizations: {e}")
        return []

    def list_projects(self, organization_id: str) -> list[McpProject]:
        try:
            result = self.call_tool("list_projects", {"organization_id": organization_id})
            data = self._parse_tool_json(result)
            return [McpProject(**p) for p in data.get("projects", [])]
        except Exception as e:
            logger.warning(f"Failed to list projects: {e}")
        return []

    def create_issue(self, project_id: str, title: str, description: str = None, priority: str = None) -> Optional[str]:
        params = {
            "project_id": project_id,
            "title": title,
        }
        if description:
            params["description"] = description
        if priority:
            params["priority"] = priority

        try:
            result = self.call_tool("create_issue", params)
            data = self._parse_tool_json(result)
            return data.get("issue_id", "")
        except Exception as e:
            logger.error(f"Failed to create issue: {e}")
        return None

    def list_issues(
        self,
        project_id: str,
        status: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[McpIssue]:
        params = {
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
        }
        if status:
            params["status"] = status

        try:
            result = self.call_tool("list_issues", params)
            data = self._parse_tool_json(result)
            return [McpIssue(
                id=i["id"],
                simple_id=i.get("simple_id", ""),
                title=i["title"],
                status=i["status"],
                priority=i.get("priority")
            ) for i in data.get("issues", [])]
        except Exception as e:
            logger.warning(f"Failed to list issues: {e}")
        return []

    def list_all_issues(self, project_id: str, status: str = None, page_size: int = 100) -> list[McpIssue]:
        issues: list[McpIssue] = []
        offset = 0

        while True:
            batch = self.list_issues(
                project_id=project_id,
                status=status,
                limit=page_size,
                offset=offset,
            )
            if not batch:
                break

            issues.extend(batch)
            if len(batch) < page_size:
                break
            offset += len(batch)

        return issues

    def delete_issue(self, issue_id: str) -> bool:
        try:
            self.call_tool("delete_issue", {"issue_id": issue_id})
            return True
        except Exception as e:
            logger.error(f"Failed to delete issue {issue_id}: {e}")
        return False

    def update_issue(
        self,
        issue_id: str,
        status: str = None,
        title: str = None,
        description: str = None,
        priority: str = None,
    ) -> bool:
        params = {"issue_id": issue_id}
        if status is not None:
            params["status"] = status
        if title is not None:
            params["title"] = title
        if description is not None:
            params["description"] = description
        if priority is not None:
            params["priority"] = priority

        try:
            self.call_tool("update_issue", params)
            return True
        except Exception as e:
            logger.error(f"Failed to update issue {issue_id}: {e}")
        return False

    def create_issue_relationship(
        self,
        issue_id: str,
        related_issue_id: str,
        relationship_type: str,
    ) -> bool:
        try:
            result = self.call_tool("create_issue_relationship", {
                "issue_id": issue_id,
                "related_issue_id": related_issue_id,
                "relationship_type": relationship_type,
            })
            if result.get("isError"):
                logger.warning(f"create_issue_relationship returned error: {result}")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to create relationship {issue_id} -{relationship_type}-> {related_issue_id}: {e}")
        return False

    def list_tags(self, project_id: str) -> list[dict]:
        try:
            result = self.call_tool("list_tags", {"project_id": project_id})
            data = self._parse_tool_json(result)
            return data.get("tags", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"Failed to list tags: {e}")
        return []

    def add_issue_tag(self, issue_id: str, tag_id: str) -> bool:
        try:
            self.call_tool("add_issue_tag", {"issue_id": issue_id, "tag_id": tag_id})
            return True
        except Exception as e:
            logger.error(f"Failed to add tag {tag_id} to issue {issue_id}: {e}")
        return False

    def get_issue(self, issue_id: str) -> dict:
        try:
            result = self.call_tool("get_issue", {"issue_id": issue_id})
            data = self._parse_tool_json(result)
            if isinstance(data.get("issue"), dict):
                return data["issue"]
            return data
        except Exception as e:
            logger.warning(f"Failed to get issue {issue_id}: {e}")
        return {}

    def list_repos(self) -> list[dict]:
        try:
            result = self.call_tool("list_repos", {})
            data = self._parse_tool_json(result)
            if result.get("isError") or data.get("success") is False:
                error = data.get("error") or "unknown error"
                details = data.get("details")
                raise RuntimeError(f"{error}" + (f": {details}" if details else ""))
            return data.get("repos", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"Failed to list repos: {e}")
            raise

    def get_repo(self, repo_id: str) -> dict:
        try:
            result = self.call_tool("get_repo", {"repo_id": repo_id})
            data = self._parse_tool_json(result)
            if isinstance(data.get("repo"), dict):
                return data["repo"]
            return data
        except Exception as e:
            logger.warning(f"Failed to get repo {repo_id}: {e}")
            return {}

    def list_workspaces(
        self,
        archived: bool = None,
        pinned: bool = None,
        branch: str = None,
        name_search: str = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        params = {
            "limit": limit,
            "offset": offset,
        }
        if archived is not None:
            params["archived"] = archived
        if pinned is not None:
            params["pinned"] = pinned
        if branch:
            params["branch"] = branch
        if name_search:
            params["name_search"] = name_search

        try:
            result = self.call_tool("list_workspaces", params)
            data = self._parse_tool_json(result)
            if result.get("isError") or data.get("success") is False:
                error = data.get("error") or "unknown error"
                details = data.get("details")
                raise RuntimeError(f"{error}" + (f": {details}" if details else ""))
            return data.get("workspaces", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"Failed to list workspaces: {e}")
            raise

    def list_sessions(self, workspace_id: str) -> list[dict]:
        try:
            result = self.call_tool("list_sessions", {"workspace_id": workspace_id})
            data = self._parse_tool_json(result)
            if result.get("isError") or data.get("success") is False:
                error = data.get("error") or "unknown error"
                details = data.get("details")
                raise RuntimeError(f"{error}" + (f": {details}" if details else ""))
            return data.get("sessions", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"Failed to list sessions for workspace {workspace_id}: {e}")
            raise

    def start_workspace(
        self,
        name: str,
        executor: str,
        repo_id: str,
        branch: str,
        prompt: str = None,
        issue_id: str = None,
        variant: str = None,
        model_id: str = None,
    ) -> dict:
        params = {
            "name": name,
            "executor": executor,
            "repositories": [
                {
                    "repo_id": repo_id,
                    "branch": branch,
                }
            ],
        }
        if prompt:
            params["prompt"] = prompt
        if issue_id:
            params["issue_id"] = issue_id
        if variant:
            params["variant"] = variant
        if model_id:
            params["executor_config"] = {
                "executor": executor,
                "model_id": model_id,
            }

        try:
            result = self.call_tool("start_workspace", params)
            data = self._parse_tool_json(result)
            if result.get("isError") or data.get("success") is False:
                error = data.get("error") or "unknown error"
                details = data.get("details")
                raise RuntimeError(f"{error}" + (f": {details}" if details else ""))
            return data
        except Exception as e:
            logger.error(f"Failed to start workspace for issue {issue_id}: {e}")
            raise

    def link_workspace_issue(self, workspace_id: str, issue_id: str) -> bool:
        try:
            result = self.call_tool(
                "link_workspace_issue",
                {"workspace_id": workspace_id, "issue_id": issue_id},
            )
            data = self._parse_tool_json(result)
            if result.get("isError") or data.get("success") is False:
                error = data.get("error") or "unknown error"
                details = data.get("details")
                raise RuntimeError(f"{error}" + (f": {details}" if details else ""))
            return True
        except Exception as e:
            logger.error(f"Failed to link workspace {workspace_id} to issue {issue_id}: {e}")
            raise

    def get_execution(self, execution_id: str) -> dict:
        try:
            result = self.call_tool("get_execution", {"execution_id": execution_id})
            data = self._parse_tool_json(result)
            if isinstance(data.get("execution"), dict):
                return data["execution"]
            if isinstance(data.get("execution_process"), dict):
                return data["execution_process"]
            return data
        except Exception as e:
            logger.warning(f"Failed to get execution {execution_id}: {e}")
        return {}

    def delete_workspace(
        self,
        workspace_id: str,
        delete_remote: bool = False,
        delete_branches: bool = False,
    ) -> bool:
        params = {
            "workspace_id": workspace_id,
            "delete_remote": delete_remote,
            "delete_branches": delete_branches,
        }
        try:
            result = self.call_tool("delete_workspace", params)
            data = self._parse_tool_json(result)
            if result.get("isError") or data.get("success") is False:
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to delete workspace {workspace_id}: {e}")
        return False

    def delete_repo(self, repo_id: str, backend_url: str = None) -> tuple[bool, str]:
        """Delete a Vibe Kanban repository registration via the local backend API.

        The MCP server exposes list/get/update repo operations but not delete_repo,
        so this uses the same local backend that MCP calls internally.
        """
        success, _, message = self._backend_request(
            path=f"/api/repos/{repo_id}",
            method="DELETE",
            backend_url=backend_url,
        )
        return success, message

    def register_repo(
        self,
        path: str,
        display_name: str | None = None,
        backend_url: str = None,
    ) -> tuple[bool, dict | None, str]:
        """Register a git repository in Vibe Kanban via the local backend API."""
        payload: dict[str, str] = {"path": path}
        if display_name:
            payload["display_name"] = display_name
        return self._backend_request(
            path="/api/repos",
            method="POST",
            payload=payload,
            backend_url=backend_url,
        )

    def check_backend_health(
        self,
        backend_url: str = None,
    ) -> tuple[bool, str]:
        """Check whether the Vibe Kanban local backend is reachable."""
        success, data, message = self._backend_request(
            path="/api/health",
            method="GET",
            backend_url=backend_url,
        )
        if success:
            if isinstance(data, str) and data:
                return True, data
            return True, "OK"
        return False, message

    def resolve_backend_url(self, backend_url: str = None) -> str:
        """Resolve the Vibe Kanban backend URL from explicit config or local discovery."""
        if backend_url:
            return backend_url.rstrip("/")

        env_url = os.environ.get("VIBE_BACKEND_URL")
        if env_url:
            return env_url.rstrip("/")

        discovered = self._discover_backend_url()
        if discovered:
            return discovered

        return VIBE_BACKEND_DEFAULT_URL

    def update_repo_http(
        self,
        repo_id: str,
        updates: dict,
        backend_url: str = None,
    ) -> tuple[bool, dict | None, str]:
        """Update a Vibe Kanban repository via the local backend API."""
        return self._backend_request(
            path=f"/api/repos/{repo_id}",
            method="PUT",
            payload=updates,
            backend_url=backend_url,
        )

    def get_repo_http(
        self,
        repo_id: str,
        backend_url: str = None,
    ) -> tuple[bool, dict | None, str]:
        """Fetch a Vibe Kanban repository via the local backend API."""
        success, data, message = self._backend_request(
            path=f"/api/repos/{repo_id}",
            method="GET",
            backend_url=backend_url,
        )
        if not isinstance(data, dict):
            return success, None, message
        return success, data, message

    def get_project_repo_defaults(
        self,
        project_id: str,
        backend_url: str = None,
    ) -> tuple[bool, list[dict] | None, str]:
        """Fetch project default repos from scratch storage."""
        success, data, message = self._backend_request(
            path=f"/api/scratch/PROJECT_REPO_DEFAULTS/{project_id}",
            method="GET",
            backend_url=backend_url,
        )
        if not success:
            if message.startswith("HTTP 404") or message == "HTTP 400: Scratch not found":
                return True, None, message
            return False, None, message

        payload = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            return True, None, "Project repo defaults payload was missing."
        scratch_data = payload.get("data")
        if not isinstance(scratch_data, dict):
            return True, None, "Project repo defaults data was missing."
        repos = scratch_data.get("repos")
        if repos is None:
            return True, [], ""
        if not isinstance(repos, list):
            return False, None, "Project repo defaults were malformed."
        return True, repos, ""

    def get_workspace_http(
        self,
        workspace_id: str,
        backend_url: str = None,
    ) -> tuple[bool, dict | None, str]:
        """Fetch a workspace from the local backend API."""
        success, data, message = self._backend_request(
            path=f"/api/workspaces/{workspace_id}",
            method="GET",
            backend_url=backend_url,
        )
        if not isinstance(data, dict):
            return success, None, message
        return success, data, message

    def get_workspace_repos_http(
        self,
        workspace_id: str,
        backend_url: str = None,
    ) -> tuple[bool, list[dict] | None, str]:
        """Fetch repos attached to a workspace from the local backend API."""
        success, data, message = self._backend_request(
            path=f"/api/workspaces/{workspace_id}/repos",
            method="GET",
            backend_url=backend_url,
        )
        if not isinstance(data, list):
            return success, None, message
        return success, data, message

    def get_workspace_editor_path_http(
        self,
        workspace_id: str,
        backend_url: str = None,
    ) -> tuple[bool, str | None, str]:
        """Fetch the absolute editor path for a workspace from the local backend API."""
        success, data, message = self._backend_request(
            path=f"/api/workspaces/{workspace_id}/integration/editor/path",
            method="GET",
            backend_url=backend_url,
        )
        if not isinstance(data, dict):
            return success, None, message
        workspace_path = data.get("workspace_path")
        if not isinstance(workspace_path, str):
            return success, None, message
        return success, workspace_path, message

    def get_session_http(
        self,
        session_id: str,
        backend_url: str = None,
    ) -> tuple[bool, dict | None, str]:
        """Fetch a session from the local backend API."""
        success, data, message = self._backend_request(
            path=f"/api/sessions/{session_id}",
            method="GET",
            backend_url=backend_url,
        )
        if not isinstance(data, dict):
            return success, None, message
        return success, data, message

    def get_workspace_summary_http(
        self,
        workspace_id: str,
        archived: bool = False,
        backend_url: str = None,
    ) -> tuple[bool, dict | None, str]:
        """Fetch summary information for a workspace from the local backend API."""
        success, data, message = self._backend_request(
            path="/api/workspaces/summaries",
            method="POST",
            payload={"archived": archived},
            backend_url=backend_url,
        )
        if not success:
            return False, None, message
        if not isinstance(data, dict):
            return False, None, "Workspace summaries payload was malformed."
        summaries = data.get("summaries")
        if not isinstance(summaries, list):
            return False, None, "Workspace summaries list was missing."
        for summary in summaries:
            if isinstance(summary, dict) and str(summary.get("workspace_id") or "") == str(workspace_id):
                return True, summary, ""
        return False, None, f"Workspace summary for {workspace_id} was not found."

    def set_project_repo_defaults(
        self,
        project_id: str,
        repos: list[dict],
        backend_url: str = None,
    ) -> tuple[bool, dict | None, str]:
        """Persist project default repos to scratch storage."""
        return self._backend_request(
            path=f"/api/scratch/PROJECT_REPO_DEFAULTS/{project_id}",
            method="PUT",
            payload={
                "payload": {
                    "type": "PROJECT_REPO_DEFAULTS",
                    "data": {"repos": repos},
                }
            },
            backend_url=backend_url,
        )

    def _backend_request(
        self,
        path: str,
        method: str,
        payload: dict | None = None,
        backend_url: str = None,
    ) -> tuple[bool, Any, str]:
        """Call the Vibe Kanban local backend and unwrap ApiResponse envelopes."""
        base_url = self.resolve_backend_url(backend_url)
        url = f"{base_url}{path}"
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                data, message = self._parse_backend_response_body(response_body)
                if 200 <= response.status < 300:
                    return True, data, message
                return False, data, f"HTTP {response.status}: {response_body}"
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            _, message = self._parse_backend_response_body(body_text)
            if message:
                return False, None, f"HTTP {e.code}: {message}"
            return False, None, f"HTTP {e.code}: {body_text}"
        except urllib.error.URLError as e:
            return False, None, f"Could not connect to Vibe Kanban backend at {base_url}: {e.reason}"
        except Exception as e:
            return False, None, str(e)

    @staticmethod
    def _parse_backend_response_body(body: str) -> tuple[Any, str]:
        if not body:
            return None, ""
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None, body

        if isinstance(parsed, dict):
            data = parsed.get("data")
            message = parsed.get("message")
            if isinstance(message, str):
                return data, message
            return data, ""
        return None, body

    @staticmethod
    def _discover_backend_url() -> str | None:
        """Probe listening localhost ports and return the first Vibe Kanban backend health endpoint."""
        ports = McpClient._list_local_listening_ports()
        for port in ports:
            candidate = f"http://127.0.0.1:{port}"
            request = urllib.request.Request(f"{candidate}/api/health", method="GET")
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    if not (200 <= response.status < 300):
                        continue
                    body = response.read().decode("utf-8", errors="replace")
                    parsed = json.loads(body)
                    if isinstance(parsed, dict) and parsed.get("success") is True:
                        return candidate
            except Exception:
                continue
        return None

    @staticmethod
    def _list_local_listening_ports() -> list[int]:
        try:
            result = subprocess.run(
                ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return []

        ports: list[int] = []
        seen: set[int] = set()
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 9:
                continue
            endpoint = parts[8]
            host_port = endpoint.rsplit("->", 1)[0]
            if ":" not in host_port:
                continue
            host, port_str = host_port.rsplit(":", 1)
            host = host.strip()
            if host not in {"127.0.0.1", "localhost", "*"} and not host.endswith(".localhost"):
                continue
            try:
                port = int(port_str)
            except ValueError:
                continue
            if port < 1024 or port in seen:
                continue
            seen.add(port)
            ports.append(port)
        return ports

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def format_description(task: dict, dispatch: dict) -> str:
    scoring = dispatch.get("scoring", {})
    estimate_hours = task.get("estimate_hours")
    story_points = task.get("story_points")

    lines = [
        f"**Task ID:** {task.get('task_id', 'Unknown')}",
        f"**Dispatched Role:** {dispatch.get('recommended_role', 'Unassigned')}",
        f"**Owner Type:** {dispatch.get('owner_type', task.get('owner_type', 'unknown'))}",
        f"**Autonomy Level:** {dispatch.get('autonomy_level', 'unknown')}",
    ]

    if estimate_hours is not None:
        lines.append(f"**Estimate:** {estimate_hours} hours")
    if story_points is not None:
        lines.append(f"**Story Points:** {story_points}")
    if scoring:
        lines.append(f"**Dispatch Score:** {dispatch.get('total_score', 0)}/8")
        score_line = _format_score_line(scoring)
        if score_line:
            lines.append(f"**Scoring Breakdown:** {score_line}")

    acceptance_criteria = task.get("acceptance_criteria")
    if acceptance_criteria:
        lines.extend([
            "",
            "**Acceptance Criteria:**",
            acceptance_criteria,
        ])

    dependencies = task.get("dependencies", [])
    if dependencies:
        lines.append("")
        lines.append(f"**Dependencies:** {', '.join(dependencies)}")

    reasoning = dispatch.get("reasoning")
    if reasoning:
        lines.extend([
            "",
            "**Dispatch Reasoning:**",
            reasoning,
        ])

    lines.extend([
        "",
        "**Task Description:**",
        task.get("description", ""),
    ])

    return "\n".join(lines)


def _load_decomposed_tasks(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        decomposed_data = json.load(f)

    tasks = []
    for story in decomposed_data.get("stories", []):
        story_id = story.get("id", "")
        for task in story.get("tasks", []):
            tasks.append({
                "story_id": story_id,
                **task,
            })
    return tasks


def _load_dispatches(path: str) -> dict[str, dict]:
    with open(path, "r", encoding="utf-8") as f:
        dispatched_data = json.load(f)

    return {
        dispatch["task_id"]: dispatch
        for dispatch in dispatched_data.get("dispatches", [])
        if dispatch.get("task_id")
    }


def _resolve_project(client: McpClient, project_name: str) -> tuple[Optional[McpOrganization], Optional[McpProject], list[McpProject]]:
    organizations = client.list_organizations()
    if not organizations:
        return None, None, []

    org = organizations[0]
    projects = client.list_projects(org.id)
    for project in projects:
        if project.name == project_name:
            return org, project, projects

    return org, None, projects


def _resolve_project_by_name_or_id(
    client: McpClient,
    project_name: str = None,
    project_id: str = None,
) -> tuple[Optional[McpOrganization], Optional[McpProject], list[McpProject]]:
    organizations = client.list_organizations()
    if not organizations:
        return None, None, []

    org = organizations[0]
    projects = client.list_projects(org.id)
    for project in projects:
        if project_id and project.id == project_id:
            return org, project, projects
        if project_name and project.name == project_name:
            return org, project, projects

    return org, None, projects


def _looks_like_scrumai_issue_title(title: str) -> bool:
    if not title.startswith("[") or "] " not in title:
        return False
    prefix = title[1:].split("]", 1)[0]
    return bool(prefix) and "-" in prefix


def _is_scrumai_issue(client: McpClient, issue: McpIssue) -> bool:
    details = client.get_issue(issue.id)
    description = details.get("description") if isinstance(details, dict) else None
    if isinstance(description, str) and all(marker in description for marker in SCRUMAI_DESCRIPTION_MARKERS):
        return True

    return _looks_like_scrumai_issue_title(issue.title)


def run_mcp_export(args):
    decomposed_path = getattr(args, "decomposed", "decomposed_task.json")

    if not os.path.exists(decomposed_path):
        print(f"Error: {decomposed_path} not found.")
        return False

    if not os.path.exists(args.dispatched):
        print(f"Error: {args.dispatched} not found.")
        return False

    tasks = _load_decomposed_tasks(decomposed_path)
    dispatches = _load_dispatches(args.dispatched)

    if not tasks:
        print(f"Error: No tasks found in {decomposed_path}.")
        return False

    export_items = []
    missing_dispatches = []
    for task in tasks:
        task_id = task.get("task_id", "")
        dispatch = dispatches.get(task_id)
        if not dispatch:
            missing_dispatches.append(task_id)
            continue
        export_items.append((task, dispatch))

    if not export_items:
        print(f"Error: No dispatched tasks matched tasks from {decomposed_path}.")
        return False

    if missing_dispatches:
        print(
            "Warning: Skipping tasks missing dispatch results:",
            ", ".join(missing_dispatches),
        )

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()

    try:
        print("Fetching organizations...")
        org, project, projects = _resolve_project(client, args.project_name)
        if not org:
            print("Error: No organizations found.")
            print("Please make sure you are signed in to Vibe Kanban.")
            return False

        print(f"Using organization: {org.name}")

        if not project:
            print(f"Project '{args.project_name}' not found.")
            print("Available projects:", [p.name for p in projects])
            print("\nNote: MCP Server cannot create projects yet.")
            print("Please create the project in Vibe Kanban UI first.")
            return False

        project_id = project.id
        print(f"Found project: {project.name}")

        print(f"Fetching existing tasks in project...")
        existing_tasks = client.list_all_issues(project_id)
        existing_titles = {t.title for t in existing_tasks}
        existing_title_to_id = {t.title: t.id for t in existing_tasks}
        print(f"Found {len(existing_tasks)} existing tasks")

        # Identify which tasks have at-export-time blockers: any dependency
        # that is itself being exported in this batch. These start in Backlog
        # and get promoted to "To do" by watch-kanban once their blockers are Done.
        exported_task_ids = {
            t.get("task_id") for t, _ in export_items if t.get("task_id")
        }
        has_blocker: dict[str, bool] = {}
        for task, _ in export_items:
            task_id = task.get("task_id")
            if not task_id:
                continue
            deps = task.get("dependencies", [])
            has_blocker[task_id] = any(d in exported_task_ids for d in deps)

        tasks_inserted = 0
        tasks_parked = 0
        task_id_to_issue_id: dict[str, str] = {}

        for task, dispatch in export_items:
            story_id = task.get("story_id", "")
            title = f"[{story_id}] {task.get('title', 'Unnamed Task')}"

            if title in existing_titles:
                print(f"Task already exists: {title}")
                task_id = task.get("task_id")
                if task_id:
                    task_id_to_issue_id[task_id] = existing_title_to_id[title]
                continue

            description = format_description(task, dispatch)

            priority = None
            risk_score = dispatch.get("scoring", {}).get("risk", {}).get("score")
            if risk_score == 2:
                priority = "high"

            issue_id = client.create_issue(
                project_id=project_id,
                title=title,
                description=description,
                priority=priority
            )
            if not issue_id:
                print(f"Failed to create: {title}")
                continue

            tasks_inserted += 1
            task_id = task.get("task_id")
            if task_id:
                task_id_to_issue_id[task_id] = issue_id

            if task_id and has_blocker.get(task_id):
                if client.update_issue(issue_id=issue_id, status="Backlog"):
                    tasks_parked += 1
                    print(f"Created (parked in Backlog): {title} (ID: {issue_id})")
                else:
                    print(f"Created but failed to park in Backlog: {title} (ID: {issue_id})")
            else:
                print(f"Created (To do): {title} (ID: {issue_id})")

        relationships_created = 0
        relationships_skipped = 0
        for task, _ in export_items:
            task_id = task.get("task_id")
            blocked_issue_id = task_id_to_issue_id.get(task_id)
            if not blocked_issue_id:
                continue
            for dep_task_id in task.get("dependencies", []):
                blocker_issue_id = task_id_to_issue_id.get(dep_task_id)
                if not blocker_issue_id:
                    relationships_skipped += 1
                    continue
                if client.create_issue_relationship(
                    issue_id=blocker_issue_id,
                    related_issue_id=blocked_issue_id,
                    relationship_type="blocking",
                ):
                    relationships_created += 1
                    print(f"  → {dep_task_id} blocks {task_id}")

        if relationships_created or relationships_skipped:
            print(
                f"\nCreated {relationships_created} blocking relationships"
                + (f" (skipped {relationships_skipped} unresolved deps)" if relationships_skipped else "")
            )

        # Persist task_id -> issue_id mapping so watch-kanban can resolve issues
        # without having to re-fetch and title-match every tick.
        mapping_path = getattr(args, "mapping", "kanban_mapping.json")
        mapping_payload = {
            "project_id": project_id,
            "project_name": project.name,
            "organization_id": org.id,
            "decomposed_path": os.path.abspath(decomposed_path),
            "task_to_issue": task_id_to_issue_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(mapping_path, "w", encoding="utf-8") as f:
                json.dump(mapping_payload, f, indent=2)
            print(f"Saved Kanban mapping to {mapping_path}")
        except Exception as e:
            print(f"Warning: failed to write mapping file {mapping_path}: {e}")

        print(
            f"\nSuccessfully created {tasks_inserted} tasks in Vibe Kanban via MCP"
            + (f" ({tasks_parked} parked in Backlog awaiting blockers)." if tasks_parked else ".")
        )
        return True

    except Exception as e:
        print(f"MCP Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        client.close()


DONE_STATUS_NAMES = {"Done", "done", "DONE"}
BACKLOG_STATUS_NAMES = {"Backlog", "backlog", "BACKLOG"}


def _build_dependency_graph(decomposed_path: str) -> dict[str, list[str]]:
    """Return a mapping of task_id -> list of dependency task_ids."""
    tasks = _load_decomposed_tasks(decomposed_path)
    graph: dict[str, list[str]] = {}
    for task in tasks:
        task_id = task.get("task_id")
        if task_id:
            graph[task_id] = list(task.get("dependencies", []))
    return graph


def run_mcp_watch(args):
    mapping_path = getattr(args, "mapping", "kanban_mapping.json")

    if not os.path.exists(mapping_path):
        print(f"Error: mapping file '{mapping_path}' not found.")
        print("Run export-kanban first so it writes the task_id -> issue_id mapping.")
        return False

    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping = json.load(f)

    project_id = mapping.get("project_id")
    project_name = mapping.get("project_name", "<unknown>")
    task_to_issue: dict[str, str] = mapping.get("task_to_issue", {})

    if not project_id:
        print(f"Error: mapping file '{mapping_path}' is missing project_id.")
        return False

    if not task_to_issue:
        print(f"No tasks to watch in '{mapping_path}' (task_to_issue is empty).")
        print("This can happen when all tasks were already exported. Nothing to do.")
        return True

    decomposed_path = getattr(args, "decomposed", None) or mapping.get(
        "decomposed_path", "decomposed_task.json"
    )
    if not os.path.exists(decomposed_path):
        print(f"Error: decomposed file '{decomposed_path}' not found.")
        return False

    dep_graph = _build_dependency_graph(decomposed_path)

    # issue_id -> task_id reverse lookup, so we can check blocker statuses by task_id
    issue_to_task = {v: k for k, v in task_to_issue.items()}

    interval = max(1, int(getattr(args, "interval", 5) or 5))
    run_once = bool(getattr(args, "once", False))

    print(f"Watching project '{project_name}' (project_id={project_id})")
    print(f"Tracking {len(task_to_issue)} tasks from {decomposed_path}")
    print(f"Mode: {'single scan' if run_once else f'continuous (every {interval}s)'}")
    print("Press Ctrl+C to stop.\n")

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()

    try:
        while True:
            issues = client.list_all_issues(project_id)
            # task_id -> current status string
            task_status: dict[str, str] = {}
            for issue in issues:
                task_id = issue_to_task.get(issue.id)
                if task_id:
                    task_status[task_id] = issue.status

            promoted_this_tick = 0
            still_backlog = 0

            for task_id, issue_id in task_to_issue.items():
                current_status = task_status.get(task_id)
                if current_status not in BACKLOG_STATUS_NAMES:
                    continue
                still_backlog += 1

                deps = dep_graph.get(task_id, [])
                # Only consider deps that we actually exported; external deps are ignored.
                internal_deps = [d for d in deps if d in task_to_issue]
                if not internal_deps:
                    # Parked with no trackable blockers -> promote immediately.
                    pending = []
                else:
                    pending = [
                        d for d in internal_deps
                        if task_status.get(d) not in DONE_STATUS_NAMES
                    ]

                if pending:
                    continue

                if client.update_issue(issue_id=issue_id, status="To do"):
                    promoted_this_tick += 1
                    print(
                        f"[unblock] {task_id} promoted Backlog -> To do "
                        f"(all blockers Done: {', '.join(internal_deps) if internal_deps else 'none'})"
                    )
                else:
                    print(f"[warn] failed to promote {task_id} (issue {issue_id})")

            if promoted_this_tick:
                print(f"  promoted {promoted_this_tick} this tick; {still_backlog - promoted_this_tick} still backlogged")

            if run_once:
                break

            # Stop if nothing remains in Backlog -- all tasks are unlocked.
            remaining_backlog = still_backlog - promoted_this_tick
            if remaining_backlog == 0 and still_backlog == 0:
                # Either everything already promoted, or nothing was ever parked.
                # If it's stable with no backlog items for one full tick, exit.
                print("No tasks remain in Backlog. Watcher exiting.")
                break

            time.sleep(interval)

        return True

    except KeyboardInterrupt:
        print("\nWatcher stopped by user.")
        return True
    except Exception as e:
        print(f"Watch error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        client.close()


def run_mcp_clear(args):
    if not getattr(args, "yes", False):
        print("Refusing to delete issues without --yes.")
        print("Re-run with --yes to remove all tickets from the target Vibe Kanban project.")
        return False

    print("Starting Vibe Kanban MCP Server...")
    client = McpClient()

    try:
        print("Fetching organizations...")
        org, project, projects = _resolve_project(client, args.project_name)
        if not org:
            print("Error: No organizations found.")
            print("Please make sure you are signed in to Vibe Kanban.")
            return False

        print(f"Using organization: {org.name}")

        if not project:
            print(f"Project '{args.project_name}' not found.")
            print("Available projects:", [p.name for p in projects])
            return False

        print(f"Found project: {project.name}")
        print("Fetching all tasks in project...")
        issues = client.list_all_issues(project.id)
        if not issues:
            print("No tasks found. Nothing to delete.")
            return True

        scrumai_issues = [issue for issue in issues if _is_scrumai_issue(client, issue)]
        if not scrumai_issues:
            print("No ScrumAI-exported tasks found. Nothing to delete.")
            return True

        print(f"Deleting {len(scrumai_issues)} ScrumAI-exported tasks...")
        deleted = 0

        for issue in scrumai_issues:
            if client.delete_issue(issue.id):
                deleted += 1
                print(f"Deleted: {issue.title} ({issue.simple_id or issue.id})")
            else:
                print(f"Failed to delete: {issue.title} ({issue.simple_id or issue.id})")

        if deleted != len(scrumai_issues):
            print(
                f"\nDeleted {deleted}/{len(scrumai_issues)} ScrumAI-exported tasks from project '{project.name}'."
            )
            return False

        print(
            f"\nSuccessfully deleted all {deleted} ScrumAI-exported tasks from project '{project.name}'."
        )
        return True

    except Exception as e:
        print(f"MCP Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        client.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export ScrumAI tasks to Vibe Kanban via MCP")
    parser.add_argument("--decomposed", default="decomposed_task.json")
    parser.add_argument("--dispatched", default="dispatched_task.json")
    parser.add_argument("--project-name", default="ScrumAI Project")
    args = parser.parse_args()
    run_mcp_export(args)
