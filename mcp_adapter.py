import json
import subprocess
import threading
import queue
import time
import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MCP_SERVER_CMD = ["npx", "-y", "vibe-kanban@latest", "--mcp"]
MCP_CALL_TIMEOUT_SECONDS = 60
MCP_STARTUP_TIMEOUT_SECONDS = 120
MCP_RESPONSE_POLL_INTERVAL_SECONDS = 1

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

    def list_organizations(self) -> list[McpOrganization]:
        try:
            result = self.call_tool("list_organizations", {})
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                data = json.loads(content[0]["text"])
                return [McpOrganization(**org) for org in data.get("organizations", [])]
        except Exception as e:
            logger.warning(f"Failed to list organizations: {e}")
        return []

    def list_projects(self, organization_id: str) -> list[McpProject]:
        try:
            result = self.call_tool("list_projects", {"organization_id": organization_id})
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                data = json.loads(content[0]["text"])
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
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                data = json.loads(content[0]["text"])
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
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                data = json.loads(content[0]["text"])
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
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                data = json.loads(content[0]["text"])
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
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                data = json.loads(content[0]["text"])
                if isinstance(data, dict):
                    if isinstance(data.get("issue"), dict):
                        return data["issue"]
                    return data
        except Exception as e:
            logger.warning(f"Failed to get issue {issue_id}: {e}")
        return {}

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

    if not project_id or not task_to_issue:
        print(f"Error: mapping file '{mapping_path}' is missing project_id or task_to_issue.")
        return False

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
