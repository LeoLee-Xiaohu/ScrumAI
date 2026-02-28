# Vibe Kanban Integration & Configuration Guide

This guide explains how to connect **ScrumAI** with **Vibe Kanban** to transform AI-generated task decompositions and dispatch evaluations into actionable Kanban boards.

## 1. Prerequisites

*   **ScrumAI** project set up locally.
*   **Vibe Kanban** installed or available via `npx`.
    *   If not installed, you can run it using: `npx vibe-kanban`.
    *   Ensure you have run `vibe-kanban` at least once so the local database is initialized.

## 2. Integration Overview

The integration uses a direct SQLite adapter (`vibe_kanban_adapter.py`) located in the ScrumAI root directory. It performs the following:
1.  Reads `decomposed_task.json` (the output of `python main.py decompose`).
2.  Reads `dispatch_evaluation.json` (the output of `python main.py evaluate-dispatch`).
3.  Injects them into the Vibe Kanban local database.

## 3. Database Configuration

The integration script needs to locate the Vibe Kanban SQLite database (`db.v2.sqlite`).

### Default Path
By default, the script automatically searches in standard OS locations:
*   **macOS:** `~/Library/Application Support/ai.bloop.vibe-kanban/db.v2.sqlite`
*   **Linux:** `~/.local/share/vibe-kanban/db.v2.sqlite`

### Custom Path
If you are running Vibe Kanban in a custom location (e.g., a local dev build) or on a different OS, you can specify the database path using the `--db` flag:

```bash
python main.py export-kanban --db "/path/to/your/db.v2.sqlite"
```

## 4. Usage Instructions

### Step 1: Generate Tasks
Run your usual ScrumAI workflow to generate the decomposed tasks and evaluations:

```bash
uv run python main.py decompose -t "Your Goal Description"
uv run python main.py dispatch
uv run python main.py evaluate-dispatch
```

### Step 2: Export to Vibe Kanban
Use the `export-kanban` command to sync the results:

```bash
uv run python main.py export-kanban
```

**Options:**
*   `--project-name`: Specify the project name in Vibe Kanban (Default: "ScrumAI Project").
*   `-i` / `--decomposed`: Custom path to decomposed JSON.
*   `-e` / `--evaluation`: Custom path to evaluation JSON.

## 5. How it Works (Data Mapping)

### Task Details
Every task in Vibe Kanban will have a rich Markdown description containing:
*   **Role:** The AI/Human role assigned to the task.
*   **Estimate:** Estimated hours/story points.
*   **Acceptance Criteria:** The specific criteria for task completion.
*   **Dispatch Evaluation Alerts:** Critical feedback from the evaluation phase (e.g., "Too risky for Junior Developer").

### Avoid Duplicates
The script checks for existing tasks by title within the specified project. If a task with the same name already exists in the "ScrumAI Project", it will be skipped to prevent clutter.

### Repository Linking (Visibility)
To ensure the project is visible in the Vibe Kanban dashboard, the adapter registers the current directory (`ScrumAI`) as a Repository (`repos` table) and joins it to the Kanban project (`project_repos` table). Without this link, the project will exist in the database but remain hidden in the UI.

## 6. Troubleshooting

*   **"Database not found":** Ensure Vibe Kanban has been launched at least once. Use the `--db` flag to point to the correct file if yours is non-standard.
*   **Tasks not showing up:** Refresh the Vibe Kanban UI (typically in your browser at `http://localhost:3000`).
*   **Syntax Error:** Ensure you are using Python 3.10+ (recommended: run via `uv run python`).
