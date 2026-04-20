# ScrumAI Prompt Playground

A CLI tool for debugging and testing AI prompts used by the ScrumAI Forge Jira plugin (`scrumai-forge`). Allows prompt engineers to iterate on prompts locally without deploying to Jira.

## Features

- **Brainstorm** — 4-phase Socratic dialogue for exploring ideas (from `brainstorm-prompts.ts`)
- **Issue Scoring** — 5-dimension readiness scoring (from `issue-scorer.ts`)
- **Task Decomposition** — Goal → sub-task tree decomposition
- **Role Dispatch** — AI/Human role assignment using 3-dimension delegation scoring
- **Dispatch Evaluation** — Evaluate accuracy of role assignments

## Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) (recommended for dependency management)

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd ScrumAI
    ```

2.  **Install dependencies:**
    Use `uv` to sync dependencies from `pyproject.toml`.
    ```bash
    uv sync
    ```

## Configuration

This project supports OpenAI-compatible APIs and Google Gemini.

### Option 1: Google Gemini (Recommended)

1.  **Get a Google Gemini API Key:**
    - Visit [Google AI Studio](https://aistudio.google.com/).
    - Sign in with your Google account.
    - Click on **"Get API key"** (or "Create API key").
    - Copy your key.

2.  **Configure environment variables:**
    - Copy the example environment file:
      ```bash
      cp .env.example .env
      ```
    - Open `.env` in your text editor and paste your API key:
      ```ini
      GEMINI_API_KEY=your_actual_api_key_here
      GEMINI_MODEL=gemini-2.5-flash
      ```

### Option 2: OpenAI-Compatible API

Configure your `.env` file:
```ini
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1  # optional, for custom endpoints
OPENAI_MODEL=gpt-4o
```

## Usage

Run commands using `uv run` to ensure it uses the correct virtual environment.

### Interactive Brainstorm

```bash
uv run python main.py brainstorm
uv run python main.py brainstorm -f ticket.md  # With context
```

### Score Issue Readiness

```bash
uv run python main.py score -f ticket.md
uv run python main.py score -t "Build a login page with auth"
```

### Decompose a Goal into Tasks

```bash
uv run python main.py decompose -t "Build a REST API for item management"
uv run python main.py decompose -f goal.md
uv run python main.py decompose -f goal.md -o my_tasks.json
```

### Dispatch Roles for Tasks

Reads `decomposed_task.json` and assigns roles with autonomy levels:

```bash
uv run python main.py dispatch
uv run python main.py dispatch -f my_tasks.json -o my_dispatch.json
```

### Evaluate Dispatch Accuracy

Compares AI-generated role assignments to original human assignments:

```bash
uv run python main.py evaluate-dispatch
uv run python main.py evaluate-dispatch -i decomposed_task.json -d dispatched_task.json
uv run python main.py evaluate-dispatch -o my_evaluation.json
```

### Export Dispatched Tasks to Vibe Kanban

Exports tasks from `decomposed_task.json` enriched with dispatch results from
`dispatched_task.json`:

```bash
uv run python main.py export-kanban -d my_dispatch.json --project-name "Your Project Name"
uv run python main.py export-kanban --project-name "Your Project Name"  # uses dispatched_task.json by default
```

### Remove ScrumAI Tickets from a Vibe Kanban Project

Deletes only tickets recognized as ScrumAI exports in the target Vibe Kanban project via MCP:

```bash
uv run python main.py clear-kanban --project-name "Your Project Name" --yes
```

The command identifies ScrumAI tickets by the Markdown markers added during export, with a title-pattern fallback. `--yes` is required because this operation is destructive.

### List Available Prompts

```bash
uv run python main.py prompts
```

## Output Files

- `decomposed_task.json` — Task decomposition results
- `dispatched_task.json` — Role dispatch results
- `dispatch_evaluation.json` — Evaluation of dispatch accuracy

## Architecture

```
scrumai-prompts/
├── prompts/              # Prompt templates (one .md file per prompt type)
│   ├── brainstorm.md
│   ├── issue_scoring.md
│   ├── task_decomposition.md
│   ├── role_dispatch.md
│   └── dispatch_evaluation.md
├── models/               # Pydantic models matching scrumai-forge TypeScript types
│   ├── brainstorm.py
│   ├── scoring.py
│   ├── task.py
│   ├── role.py
│   └── dispatch_evaluation.py
├── runners/              # CLI runners for each prompt type
│   ├── brainstorm.py
│   ├── scoring.py
│   ├── task.py
│   ├── dispatch.py
│   └── dispatch_evaluation.py
├── client.py             # LLM client (OpenAI-compatible + Google Genai)
├── mcp_adapter.py        # Vibe Kanban MCP Client
└── main.py              # CLI entry point
```

## Type Mapping

Pydantic models mirror TypeScript types from `scrumai-forge`:
- `models/brainstorm.py` ↔ `src/types/brainstorm.ts` + `src/lib/brainstorm-prompts.ts`
- `models/scoring.py` ↔ `src/lib/issue-scorer.ts`
- `models/role.py` ↔ Role dispatch framework (Lubars & Tan, 2019)

## Prompt Management

The `prompts/` directory is the single source of truth for all ScrumAI system prompts. These files are consumed by the [ScrumAI Forge](https://github.com/oldcai/scrumai-forge) Jira plugin via GitHub raw URLs, with a 5-minute cache TTL.

**To modify prompts**: Follow the workflow in [CONTRIBUTING.md](CONTRIBUTING.md) — create an issue, submit a PR, and get team review before merging. Changes take effect automatically within 5 minutes after merge.

## Help

To see all available options:
```bash
uv run python main.py --help
uv run python main.py <command> --help
```
