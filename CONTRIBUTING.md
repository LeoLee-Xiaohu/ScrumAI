# Contributing to ScrumAI Prompts

This guide explains how to modify, test, and deploy prompt changes used by the ScrumAI Forge Jira plugin.

## How Prompts Are Consumed

The `prompts/` directory contains Markdown files that serve as system prompts for the ScrumAI Forge app (`scrumai-forge`). The Forge app fetches these prompts at runtime:

```
prompts/*.md  →  GitHub raw URL  →  scrumai-forge prompt-fetcher  →  LLM system prompt
```

- **Source**: `https://raw.githubusercontent.com/LeoLee-Xiaohu/ScrumAI/main/prompts/`
- **Cache**: 5-minute TTL with 4-layer fallback (memory → Forge Storage → GitHub → inline fallback)
- **Effect**: After merging to `main`, prompt changes take effect within 5 minutes — no Forge redeployment needed.

## Prompt Files

| File | Used By | Purpose |
|------|---------|---------|
| `brainstorm.md` | Brainstorm Wizard | 4-phase Socratic dialogue for requirement refinement |
| `issue_scoring.md` | Issue Scorer | 5-dimension AI readiness scoring (0-10) |
| `role_dispatch.md` | Role Dispatcher | 3-dimension task delegability evaluation |
| `task_decomposition.md` | Task Decomposer | Goal → Epic → Story → Task breakdown |
| `task_decomposition_with_context.md` | (CLI only) | Decomposition with `{repo_context}` injection |
| `dispatch_evaluation.md` | (CLI only) | Evaluate dispatch accuracy |

## How to Modify a Prompt

### 1. Create an Issue

Before making changes, create a GitHub issue describing:
- Which prompt you're modifying and why
- What behavior you expect to change
- Any test cases or examples

### 2. Branch and Edit

```bash
git checkout -b prompt/improve-brainstorm-scoring
# Edit the prompt file
vim prompts/brainstorm.md
```

### 3. Test Locally

Use the CLI tool to verify your changes before submitting a PR:

```bash
# Test brainstorm prompt
uv run main.py brainstorm -f ticket.md

# Test scoring prompt
uv run main.py score -t "Your test issue description"

# Test role dispatch
uv run main.py dispatch

# List all prompts
uv run main.py prompts
```

### 4. Submit a Pull Request

- Reference the issue number in the PR title
- Include before/after examples showing the behavior change
- Request review from at least one team member

### 5. After Merge

Once merged to `main`, the Forge app will automatically pick up the new prompt within 5 minutes. No deployment action is needed.

## Prompt Writing Guidelines

- **Format**: Markdown with clear section headers
- **Output specification**: Always define the expected JSON response structure
- **Language**: Support both English and Chinese where applicable
- **Scoring rubrics**: Use explicit 0-N scales with examples for each level
- **Keep prompts self-contained**: Each prompt should work independently as a system prompt

## Type Alignment

Prompt output schemas must match the TypeScript types in `scrumai-forge`:

| Prompt | Forge Type File |
|--------|----------------|
| `brainstorm.md` | `src/types/brainstorm.ts` |
| `issue_scoring.md` | `src/lib/issue-scorer.ts` |
| `role_dispatch.md` | `src/types/role-dispatch.ts` |

If you change the JSON output structure in a prompt, a corresponding type change in `scrumai-forge` may be needed. Coordinate with the Forge team.
