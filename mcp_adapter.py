import json
import subprocess
import threading
import queue
import time
import logging
import os
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MCP_SERVER_CMD = ["npx", "-y", "vibe-kanban@latest", "--mcp"]

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

        time.sleep(5)

        if self.process.poll() is not None:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"MCP server failed to start: {stderr}")

        self._start_reader()

        try:
            self._initialize()
        except Exception as e:
            stderr = ""
            if self.process.stderr:
                import select
                if select.select([self.process.stderr], [], [], 0)[0]:
                    stderr = self.process.stderr.read()
            raise RuntimeError(f"MCP initialize failed: {e}. Stderr: {stderr}")

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

    def _send_jsonrpc(self, method: str, params: dict = None, expect_response: bool = True) -> dict:
        with self.lock:
            self.request_id += 1
            req_id = self.request_id

            request = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {}
            }

            if not expect_response:
                request_str = json.dumps(request) + "\n"
                self.process.stdin.write(request_str)
                self.process.stdin.flush()
                return {}

            response_queue = queue.Queue()
            self._response_queues[req_id] = response_queue

            try:
                request_str = json.dumps(request) + "\n"
                self.process.stdin.write(request_str)
                self.process.stdin.flush()

                msg = response_queue.get(timeout=60)

                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")

                return msg.get("result", {})
            finally:
                self._response_queues.pop(req_id, None)

    def _initialize(self):
        result = self._send_jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "scrumai",
                "version": "1.0.0"
            }
        })

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

    def list_issues(self, project_id: str, status: str = None, limit: int = 100) -> list[McpIssue]:
        params = {
            "project_id": project_id,
            "limit": limit,
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
        organizations = client.list_organizations()
        if not organizations:
            print("Error: No organizations found.")
            print("Please make sure you are signed in to Vibe Kanban.")
            return False

        org_id = organizations[0].id
        print(f"Using organization: {organizations[0].name}")

        print("Fetching projects...")
        projects = client.list_projects(org_id)

        project_id = None
        for p in projects:
            if p.name == args.project_name:
                project_id = p.id
                print(f"Found project: {p.name}")
                break

        if not project_id:
            print(f"Project '{args.project_name}' not found.")
            print("Available projects:", [p.name for p in projects])
            print("\nNote: MCP Server cannot create projects yet.")
            print("Please create the project in Vibe Kanban UI first.")
            return False

        print(f"Fetching existing tasks in project...")
        existing_tasks = client.list_issues(project_id)
        existing_titles = {t.title for t in existing_tasks}
        print(f"Found {len(existing_tasks)} existing tasks")

        tasks_inserted = 0

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
            if issue_id:
                tasks_inserted += 1
                print(f"Created: {title} (ID: {issue_id})")
            else:
                print(f"Failed to create: {title}")

        print(f"\nSuccessfully created {tasks_inserted} tasks in Vibe Kanban via MCP.")
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
