# MCP Adapter (mcp_adapter.py)

The `mcp_adapter.py` is a Python-based client that implements the **Model Context Protocol (MCP)** to communicate with the **Vibe Kanban** server. It enables **ScrumAI** to export generated tasks enriched with dispatch results directly into a Vibe Kanban board.

## Overview

ScrumAI uses this adapter to bridge the gap between AI-driven project planning and real-world task management. Instead of manually copying tasks, the adapter uses the MCP JSON-RPC protocol to automate the creation of issues in Vibe Kanban.

## Key Components

### 1. `McpClient` Class
The core class that manages the lifecycle of the MCP connection:
- **Initialization:** Automatically starts the Vibe Kanban MCP server using `npx -y vibe-kanban@latest --mcp`.
- **Communication:** Implements a JSON-RPC 2.0 client over standard input/output (stdio).
- **Concurrency:** Uses a background thread for non-blocking message reading and thread-safe request/response matching.
- **Methods:**
    - `list_organizations()`: Retrieves the user's Vibe Kanban organizations.
    - `list_projects(organization_id)`: Fetches projects under a specific organization.
    - `create_issue(project_id, title, description, priority)`: Creates a new task in the specified project.
    - `list_issues(project_id)`: Fetches paginated issues from the specified project.
    - `list_all_issues(project_id)`: Walks pagination to retrieve the full issue list.
    - `delete_issue(issue_id)`: Deletes a single issue by ID.

### 2. `run_mcp_export` Function
The main orchestration function used by the `export-kanban` command:
1. Loads task data from `decomposed_task.json`.
2. Loads role dispatch results from `dispatched_task.json`.
3. Connects to the MCP server and selects the appropriate organization and project.
4. Joins decomposed tasks with their dispatch metadata and formats them into rich Markdown descriptions.
5. Skips tasks that already exist in the target project.
6. Closes the connection gracefully.

### 3. `run_mcp_clear` Function
Used by the `clear-kanban` command to delete only ScrumAI-exported issues in a target Vibe Kanban project:
1. Connects to the MCP server and resolves the requested organization/project.
2. Fetches every issue in the project using paginated reads.
3. Reads each issue's details and matches ScrumAI export markers in the description, with a title-based fallback.
4. Requires an explicit `--yes` confirmation flag before deletion.
5. Deletes matched issues one by one through the MCP `delete_issue` tool.

## Data Mapping

The adapter transforms ScrumAI's JSON output into a human-readable format for Kanban cards:

| ScrumAI Field | Vibe Kanban Field | Notes |
| :--- | :--- | :--- |
| `task_id` | Title Prefix | e.g., `[STORY-1] Task Title` |
| `title` | Issue Title | The main task name. |
| `description` | Issue Description | Formatted with task details and dispatch metadata. |
| `recommended_role` | Metadata in Description | Final dispatched role. |
| `owner_type` | Metadata in Description | Whether the task is assigned to AI or human. |
| `autonomy_level` | Metadata in Description | Manual, supervised, or autonomous execution. |
| `estimate_hours` | Metadata in Description | Time estimation. |
| `scoring.risk.score` | Priority | If risk score is `2`, the task priority is set to `high`. |

## Technical Implementation Details

- **MCP Protocol:** Uses the standard `2024-11-05` protocol version.
- **Subprocess Management:** The Vibe Kanban server runs as a subprocess. The adapter captures `stderr` for debugging if the server fails to start.
- **Timeout Handling:** Includes a 60-second timeout for RPC calls to ensure the CLI doesn't hang indefinitely.
- **Safety Guard:** Bulk deletion requires the CLI `--yes` flag and only targets issues recognized as ScrumAI exports.

## Requirements

1. **Node.js & npm:** Required to run the `vibe-kanban` server via `npx`.
2. **Authentication:** The user must be signed in to Vibe Kanban (via browser) for the MCP server to access their data.
3. **Existing Project:** The target project must be created in the Vibe Kanban UI before running the export; the MCP protocol currently does not support creating new projects.

## Troubleshooting

If the adapter fails to connect:
- Ensure `npx` is available in your system PATH.
- Verify you are logged into Vibe Kanban at `http://127.0.0.1:61652` or the production URL.
- Check if another instance of the MCP server is already running and occupying the port/resources.
