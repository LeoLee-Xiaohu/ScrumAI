# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ScrumAI Prompt Playground - A CLI tool for debugging and testing AI prompts used by the ScrumAI Forge Jira plugin (`scrumai-forge`). Allows prompt engineers to iterate on prompts locally without deploying to Jira.

## Architecture

```
scrumai-prompts/
├── prompts/           # Prompt templates (one .md file per prompt type)
│   ├── brainstorm.md          # 4-phase Socratic dialogue (from brainstorm-prompts.ts)
│   ├── issue_scoring.md       # 5-dimension readiness scoring (from issue-scorer.ts)
│   └── task_decomposition.md  # Goal → sub-task tree decomposition
├── models/            # Pydantic models matching scrumai-forge TypeScript types
│   ├── brainstorm.py          # BrainstormResponse, BrainstormScoring, etc.
│   ├── scoring.py             # ScoreResult, ScoringDimensions, etc.
│   └── task.py                # TaskDecompositionResult, Story, Task, etc.
├── runners/           # CLI runners for each prompt type
│   ├── brainstorm.py          # Interactive brainstorm session
│   ├── scoring.py             # Issue readiness scoring
│   └── task.py                # Task decomposition
├── client.py          # LLM client (OpenAI-compatible + Google Genai)
├── main.py            # CLI entry point (argparse subcommands)
└── pyproject.toml     # Project config (uv/pip)
```

## Type Mapping

Pydantic models mirror TypeScript types from `scrumai-forge`:
- `models/brainstorm.py` ↔ `src/types/brainstorm.ts` + `src/lib/brainstorm-prompts.ts`
- `models/scoring.py` ↔ `src/lib/issue-scorer.ts`
- `models/task.py` ↔ original `main.py` output format

## Development Commands

```bash
uv sync                                    # Install dependencies
python main.py brainstorm                  # Interactive brainstorm
python main.py brainstorm -f ticket.md     # With ticket context
python main.py score -f ticket.md          # Score issue readiness
python main.py decompose -t "Build X"      # Decompose a goal
python main.py prompts                     # List available prompts
```

## Adding New Prompts

1. Create `prompts/new_prompt.md` with the prompt template
2. Create `models/new_model.py` with Pydantic models for structured output
3. Create `runners/new_runner.py` with the CLI runner
4. Register the subcommand in `main.py`

## LLM Providers

Configured via `.env` file. Auto-detects provider from available API keys.
- OpenAI-compatible: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`
- Google Gemini: `GEMINI_API_KEY`, `GEMINI_MODEL`
