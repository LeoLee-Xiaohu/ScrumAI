# Scrum AI — Product Requirements Document (PRD)

> **Version:** 0.4
> **Status:** In Progress
> **Last Updated:** Mar 27, 2026

---

## 1. Product Vision (One Sentence)

**Manage AI agents like a human team**: instead of long, messy chat threads, Scrum AI turns an AI workflow into a **visible board + task tree**, where humans step in only at key blockers.

---

## 2. Background & Context

Today's "coding agents":
- **Black-box**: agents run ahead with unclear progress and uncertain correctness; or
- **babysitting**: humans must confirm every step, creating high cognitive load.

Scrum AI aims to create a **management-first** interface (board / graph) that makes agent work **transparent, auditable, and asynchronous**.

---

## 3. Target Users

1. **Product & engineering teams using Scrum/Kanban** to ship software.
2. **Teams pushing AI adoption** inside enterprises (need visibility, control, and auditability).
3. Solo builders who want parallel agent execution without losing track of "who is doing what".

---

## 4. Problems / Pain Points

1. **Information fragmentation:** agent status, logs, and artifacts are scattered — users forget which agent is doing what.
2. **Model specialization complexity:** strongest models differ in strengths; most users can't orchestrate multiple models well.
3. **Workflow transparency gap:** black-box autonomy vs. babysitting confirmation — no middle ground.
4. **Efficiency bottleneck:** single-threaded development — unable to execute multiple tasks concurrently with proper isolation.
5. **Missing human-AI collaboration:** existing tools focus on individual developers; lack mechanisms for human team collaboration and Scrum workflows.
6. **Context blindness:** task decomposition without codebase awareness produces generic subtasks that don't reflect real architecture.

---

## 5. Goals & Non-Goals

### Goals
- Convert one high-level goal into a **task tree** with clear ownership and acceptance criteria.
- Provide a **unified dashboard** for:
  - task status (To Do / In Progress / Blocked / Done),
  - assignments (AI role vs human role),
  - blockers requiring human decision,
  - evaluation results.
- Support **prompt versioning + evaluation** to know whether a prompt change improved outcomes.
- Enable **context-aware decomposition** by injecting GitHub repository context into the task planning prompt.

### Non-goals (initial)
- Solving code merge conflicts / task interference in depth.
- Full "agentic coding" execution at scale (may add later, but not required for MVP).
- Perfect generalization across all domains (demo focuses on SaaS app dev tasks).

---

## 6. Key Concepts

### 6.1 Task
A unit of work with:
- clear owner (human or AI role),
- defined inputs/context,
- defined outputs/artifacts,
- **acceptance criteria**,
- status + logs,
- optional dependencies.

### 6.2 Role vs Agent
- **Role** = responsibility/contract (e.g., "Backend Developer", "Reviewer").
- **Agent** = a runnable worker that performs a role using prompts + tools + skills.
- **Skill** = a reusable capability or knowledge module that agents can invoke (e.g., "code review", "test generation", "API design").
- One role may be backed by **multiple agents**, and one agent may implement multiple roles in early stages.
- Current roles: 3 domain-based AI roles (`Frontend Developer`, `Backend Developer`, `DevOps`) + 3 human roles (`Product Owner`, `Scrum Master`, `Reviewer`).

### 6.3 Blocker
A task state requiring human decision/approval before progress continues (e.g., choosing between design options, approving UI draft).

### 6.4 Acceptance / Evaluation
A mechanism to decide whether a task is "Done":
- human review,
- automated checks (tests, lint, schema validation),
- LLM-as-judge rubric scoring.

### 6.5 Workspace
An isolated working environment for task execution:
- Each task gets its own branch and working directory
- Enables parallel development without conflicts

### 6.6 Session
An AI Agent conversation thread within a Workspace:
- Tied to a specific AI executor
- Tracks conversation history, tool calls, and outputs

### 6.7 Sprint Context
Scrum workflow context for team collaboration:
- Sprint goal and timeline
- AI-eligible vs human-required stories classification
- Human checkpoint definitions

### 6.8 Repository Context
GitHub repository metadata and code structure injected into task decomposition:
- Fetched via GitHub API (public and private repos supported)
- Includes directory tree, key file contents, and source code summaries
- Enables context-aware, codebase-specific task decomposition

---

## 7. Product Roadmap

### Phase 1 — Task Decomposition + Role Dispatch (MVP) ✅
Intelligent task decomposition and role-based dispatch.
- User provides a high-level goal
- System produces a task tree with acceptance criteria (implemented: `decompose` CLI)
- **Context-aware decomposition**: optionally inject GitHub repo context for codebase-aligned tasks (implemented: `--repo-url`, `--branch`, `--focus-paths` flags)
- Role dispatch evaluates each task on 4 dimensions and assigns roles + autonomy levels (implemented: `dispatch` CLI)
- Dispatch evaluation: LLM-as-judge scores accuracy of role assignments (implemented: `evaluate-dispatch` CLI)
- Issue readiness scoring (implemented: `score` CLI)
- Interactive brainstorm for requirement clarification (implemented: `brainstorm` CLI)
- Export dispatched tasks to Vibe Kanban (implemented: `export-kanban` CLI via MCP)

### Phase 2 — Human-in-the-Loop
Human approval and guidance integration.
- Approval notifications and UI
- Code review integration
- Blocker management for items requiring human decision
- Dashboard showing task status and AI workspace state

### Phase 3 — Multi-Agent Collaboration
Team-style task execution with multiple AI and human agents.
- Agent execution engine with workspace isolation
- Sprint context support
- Pipeline automation (brainstorm → score → decompose → dispatch → export)
- Agentic repository retrieval: LLM-driven iterative code exploration for context (vs. current static read)

---

## 8. Functional Requirements

### 8.1 Task Management
- Create / read / update / delete tasks
- Bulk import from PRD or structured documents

### 8.2 Task Decomposition (implemented)
- LLM-powered task splitting from high-level goal into Epic → Stories → Tasks
- Outputs: subtasks, dependencies, role suggestions, acceptance criteria, execution plan
- CLI: `uv run main.py decompose -t "goal"` or `uv run main.py decompose -f goal.md`
- Optional repository context: `--repo-url https://github.com/owner/repo --branch main --focus-paths src tests`
- Output format: `decomposed_task.json`
- LLM providers: OpenAI-compatible, Google Gemini, MiniMax (via `--provider` flag)

### 8.3 Task Dispatch & Ownership (implemented)
Two-step evaluation framework per task:
- **Step 1 — Delegation scoring**: 4-dimension scoring (Complexity, Risk, Human Judgment, Domain Specificity; 0-2 each, max 8) determines `autonomy_level` (autonomous/supervised/manual) and `owner_type` (ai/human). All 4 dimensions adapted from the AI Task Delegability Framework (Lubars & Tan, NeurIPS 2019): Difficulty→Complexity, Risk→Risk, Trust→Human Judgment, Motivation→Domain Specificity (reframed for agent routing: the paper measures human desire/fit for a task; we measure which specialist agent best fits the task).
- **Step 2 — Role classification**: Task content matched to one of 6 domain-based roles (3 AI + 3 human), calibrated by few-shot examples from TaskAllocator dataset (Shafiq et al., 2021). Full dataset CSVs and paper PDFs available in `docs/`.
- CLI: `uv run main.py dispatch -f decomposed_task.json`
- Output format: `dispatched_task.json`

### 8.4 Dispatch Evaluation (implemented)
- LLM-as-judge evaluates dispatch results across 5 criteria: owner type accuracy, role precision, delegation score validity, autonomy mapping correctness, reasoning quality
- CLI: `uv run main.py evaluate-dispatch`
- Output format: `dispatch_evaluation.json`

### 8.5 Vibe Kanban Integration (implemented)
- Export dispatched tasks to Vibe Kanban project board via MCP adapter
- CLI: `uv run main.py export-kanban --project-name "My Project"`
- Clear exported tasks: `uv run main.py clear-kanban --project-name "My Project" --yes`

### 8.6 Status & Visibility
- Status states (implemented in code): `todo`, `in_progress`, `blocked`, `done`
- Future additions: `in_review`, `cancelled`, `failed`

### 8.7 Acceptance & Evaluation
- Each task must include acceptance criteria
- Evaluation methods: LLM judge, automated checks, manual approval

### 8.8 Prompt Management
- Store prompt sets by role
- Track prompt version per task run

---

## 9. LLM Provider Support

| Provider | Config | Notes |
|----------|--------|-------|
| OpenAI-compatible | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` | Default; supports DeepSeek, Groq, OpenRouter, etc. |
| Google Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` | Via `google-genai` SDK |
| MiniMax | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL` | Via Anthropic-compatible endpoint |

Provider selection: `--provider openai|gemini|minimax` flag, or `LLM_PROVIDER` env var, or auto-detect from available API keys.

---

## 10. Non-Functional Requirements

### 10.1 Performance
- Fast task decomposition response
- Real-time status updates
- Support for concurrent AI tasks

### 10.2 Security
- Guard rails to prevent sensitive information exposure
- Input validation and audit logging
- Role-based access control
- GitHub token support for private repository context

### 10.3 Scalability
- Support multiple projects and teams
- Efficient storage for large task trees
- Repository context token limiting (max 50k tokens, truncated at section boundaries)

### 10.4 Reliability
- Graceful degradation when AI services unavailable
- Task state persistence
- Timeout handling for long-running operations (MCP: configurable, LLM: per-provider)

---

## 11. Data Model

### 11.1 Task Entity
- `task_id` (uuid)
- `title`
- `description`
- `status` (enum: `todo` | `in_progress` | `blocked` | `done`)
- `role` (string: `Frontend Developer` | `Backend Developer` | `DevOps` | `Product Owner` | `Scrum Master` | `Reviewer`)
- `owner_type` (`human` | `ai`)
- `assignee` (person_id or agent_id)
- `estimate_hours` (float, optional)
- `story_points` (int, optional)
- `dependencies` (list of task_id)
- `acceptance_criteria` (markdown/text)
- `blocker_reason` (text, optional)
- `artifacts` (links: docs, code diffs, screenshots)
- `logs` (agent messages + timestamps)
- `prompt_version` (per role)
- `evaluation_score` (numeric + rationale)
- `created_at`, `updated_at`

### 11.2 Decomposition Result
- `epic` (title + description)
- `reasoning` (chain-of-thought analysis)
- `stories` (list of Story → Tasks)
- `execution_plan` (phases, critical path, total hours)
- `repo_context` (optional: repo_url, branch, fetched_at)

### 11.3 Dispatch Result
- Per task: `task_id`, `scoring` (4 dimensions), `total_score`, `recommended_role`, `owner_type`, `autonomy_level`, `reasoning`
- `summary` (overall dispatch overview)

### 11.4 Role Catalog

6 pre-defined roles (Title Case format, matching codebase convention):

#### AI Roles (domain-based)
- `Frontend Developer` — UI components, styling, client-side logic, React/Vue/Angular, responsive design. Each agent carries a domain-specific system prompt with relevant frameworks.
- `Backend Developer` — API design, server-side logic, database operations, authentication, data processing.
- `DevOps` — CI/CD pipelines, deployment, monitoring, logging, Docker, cloud configuration.

#### Human Roles
- `Product Owner` — Business decisions, priority calls, goal-setting, requirement clarification. Autonomy: manual.
- `Scrum Master` — Process management, sprint planning, team coordination, blocker resolution. Autonomy: manual.
- `Reviewer` — Quality gates, technical approval, design review, security audit. Autonomy: manual.

#### Autonomy Levels (assigned by dispatch, 4-dimension scoring, max 8)
- `autonomous` — Fully automated AI execution, no human oversight needed (total delegation score 0-2)
- `supervised` — AI executes with human review at key checkpoints (total delegation score 3-5)
- `manual` — Human-led execution, AI assists only (total delegation score 6-8)

#### Role Customization (future)
- Users can define custom roles with specific capabilities

---

## 12. UX / UI Requirements

### MVP UI Components
1. **Kanban board** with owner badges and status columns
2. **Task Tree / Graph view** showing dependencies
3. **Blocker Inbox** for items requiring human decision
4. **Activity Log** per task

*Detailed UI specifications to be defined during design phase.*

---

## 13. Acceptance Criteria for MVP Demo

A successful demo should show:
1. User submits a high-level goal via CLI (with optional repo context)
2. System generates a **task tree** with role assignments, acceptance criteria, and dependencies (`decompose`)
3. System evaluates each task and assigns roles with autonomy levels (`dispatch`)
4. Output clearly distinguishes AI-autonomous, AI-supervised, and human-manual tasks
5. Tasks can be exported to Vibe Kanban board (`export-kanban`)
6. Future: UI displays task status with at least one **human blocker** task

---

## 14. Risks & Open Questions

1. **Dispatch consistency:** LLM-based scoring may vary across runs; calibration via few-shot examples helps but does not guarantee identical results.
2. **Decomposition threshold:** what metric triggers task splitting?
3. **Evaluation reliability:** LLM-as-judge bias and repeatability
4. **Scope creep:** keep MVP focused on core features
5. **Role vs agent mapping:** how many agents per role, and when to parallelize?
6. **Repository context relevance:** current static read approach lacks semantic filtering; large repos may exceed token limits or include irrelevant code. Future: agentic retrieval or embedding-based CodeRAG.

---

## Appendix A — Glossary

- **CRUD:** Create / Read / Update / Delete
- **Kanban:** visual workflow board (To Do → Doing → Done)
- **Scrum:** iterative delivery framework (sprints, roles, ceremonies)
- **Blocker:** a condition preventing a task from progressing without external input
- **Workspace:** isolated environment for AI task execution
- **Session:** AI Agent conversation thread within a Workspace
- **Executor:** AI coding tool that performs tasks
- **Dispatch:** the process of evaluating tasks and assigning roles + autonomy levels
- **Delegation scoring:** 4-dimension evaluation (Complexity, Risk, Human Judgment, Domain Specificity) to determine autonomy level
- **Autonomy level:** degree of human oversight (autonomous / supervised / manual)
- **Repo Context:** GitHub repository structure and code content injected into task decomposition prompts
- **MCP:** Model Context Protocol — used by the Vibe Kanban adapter for task export
- **CodeRAG:** Code Retrieval-Augmented Generation — semantic code search to find relevant context for a given task

---

## Appendix B — CLI Reference

```bash
# Task decomposition
uv run main.py decompose -t "Build a REST API"
uv run main.py decompose -f goal.md
uv run main.py decompose -t "Add OAuth" --repo-url https://github.com/owner/repo
uv run main.py decompose -t "Add feature" --repo-url owner/repo --branch develop --focus-paths src tests

# Role dispatch
uv run main.py dispatch
uv run main.py dispatch -f decomposed_task.json -o dispatched.json

# Dispatch evaluation
uv run main.py evaluate-dispatch
uv run main.py evaluate-dispatch -i decomposed_task.json -d dispatched_task.json

# Issue scoring
uv run main.py score -f ticket.md
uv run main.py score -t "Build a login page"

# Brainstorm
uv run main.py brainstorm
uv run main.py brainstorm -f ticket.md

# Vibe Kanban
uv run main.py export-kanban --project-name "ScrumAI Project"
uv run main.py clear-kanban --project-name "ScrumAI Project" --yes

# LLM provider selection
uv run main.py --provider openai decompose -f goal.md
uv run main.py --provider gemini decompose -f goal.md
uv run main.py --provider minimax decompose -f goal.md
```
