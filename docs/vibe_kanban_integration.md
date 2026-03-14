# Vibe Kanban Integration Guide

This guide explains how to connect **ScrumAI** with **Vibe Kanban** to transform AI-generated task decompositions and dispatch results into actionable Kanban boards.

## 1. Prerequisites

*   **ScrumAI** project set up locally.
*   **Vibe Kanban** running on your local machine.
    *   Run: `npx vibe-kanban`
    *   Wait for it to open in your browser (typically http://127.0.0.1:61652)
    *   Sign in with GitHub or Google account
*   **Node.js/npm** (required for MCP mode).

## 2. Usage Instructions

### Step 1: Generate Tasks
Run your usual ScrumAI workflow to generate the decomposed tasks and dispatch results:

```bash
uv run python main.py decompose -t "Your Goal Description"
uv run python main.py dispatch
uv run python main.py evaluate-dispatch
```

### Step 2: Start Vibe Kanban

Open a terminal and run:

```bash
npx vibe-kanban
```

Wait for it to open in your browser, then:
1. Sign in with GitHub or Google
2. Create a new project (e.g., "ScrumAI Project")
3. Add some columns (To Do, In Progress, Done)

### Step 3: Export to Vibe Kanban

```bash
uv run python main.py export-kanban --project-name "Your Project Name"
```

**Options:**
*   `--project-name`: The project name in Vibe Kanban (Default: "ScrumAI Project")
*   `-i` / `--decomposed`: Custom path to decomposed JSON (Default: decomposed_task.json)
*   `-d` / `--dispatched`: Custom path to dispatch JSON (Default: dispatched_task.json)

## 3. How it Works (Data Mapping)

### Task Details
Every task in Vibe Kanban will have a rich Markdown description containing:
*   **Dispatched Role:** The final role assigned by the dispatch step.
*   **Owner Type / Autonomy:** Whether the task is AI or human owned, and how much autonomy it has.
*   **Dispatch Score:** Total score and per-dimension breakdown from the dispatch step.
*   **Estimate:** Estimated hours/story points.
*   **Acceptance Criteria:** The specific criteria for task completion.
*   **Task Description:** The original decomposed task details.

### Avoid Duplicates
The script checks for existing tasks by title within the specified project. If a task with the same name already exists, it will be skipped to prevent duplicates.

## 4. Troubleshooting

*   **"No organizations found":** Make sure you are signed in to Vibe Kanban in the browser.
*   **"Project not found":** Make sure the project exists in Vibe Kanban. You need to create it in the UI first (MCP mode cannot create projects yet).
*   **Tasks not showing up:** Refresh the Vibe Kanban UI.

---

**For more technical details on how the integration works, see the [MCP Adapter Documentation](mcp_adapter.md).**

## 5. Command Summary

```bash
# Full workflow
uv run python main.py decompose -t "Your goal"
uv run python main.py dispatch
uv run python main.py evaluate-dispatch
uv run python main.py export-kanban --project-name "Your Project"
```
