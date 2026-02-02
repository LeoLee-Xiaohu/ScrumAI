# Scrum AI — Product Requirements Document (PRD)

> **Version:** 0.2
> **Status:** In Progress
> **Last Updated:** Feb 2, 2026
> **Change Log:** Added Vibe Kanban integration strategy, updated role system, added non-functional requirements
---

## 1. Product Vision (One Sentence)

**Manage AI agents like a human team**: instead of long, messy chat threads, Scrum AI turns an AI workflow into a **visible board + task tree**, where humans step in only at key blockers.

---

## 2. Background & Context

Today's "coding agents":
- **Black-box**: agents run ahead with unclear progress and uncertain correctness; or
- **babysitting**: humans must confirm every step, creating high cognitive load.

Scrum AI aims to create a **management-first** interface (board / graph) that makes agent work **transparent, auditable, and asynchronous**.

### Vibe Kanban Context (v0.2 Update)

**Vibe Kanban** is an open-source AI coding agent orchestration platform by BloopAI with 1000+ commits. It provides:
- Multi-agent orchestration (Claude Code, Gemini CLI, Codex, Cursor Agent, etc.)
- Git Worktree isolation for parallel task execution
- Real-time status tracking and approval workflows

**Our differentiation**: Vibe Kanban targets individual developers. We extend it for **team collaboration** with:
- Human role system (Product Owner, Scrum Master, Reviewer)
- Scrum workflow rules and sprint context
- Deep Jira integration for enterprise teams
- Intelligent task decomposition (which Vibe Kanban lacks)

---

## 3. Target Users

1. **Product & engineering teams using Scrum/Kanban** to ship software.
2. **Teams pushing AI adoption** inside enterprises (need visibility, control, and auditability).
3. Solo builders who want parallel agent execution without losing track of "who is doing what".


---

## 4. Problems / Pain Points

1. **Information fragmentation:** agent status, logs, and artifacts are scattered → users forget which agent is doing what.
2. **Model specialization complexity:** strongest models differ in strengths; most users can't orchestrate multiple models well.
3. **Workflow transparency gap:** black-box autonomy vs. babysitting confirmation—no middle ground.
4. **Efficiency bottleneck (v0.2):** single-threaded development — unable to execute multiple tasks concurrently with proper isolation.
5. **Missing human-AI collaboration (v0.2):** existing tools (including Vibe Kanban) focus on individual developers; lack mechanisms for human team collaboration and Scrum workflows.

---

## 5. Goals & Non-Goals

### Goals (v0.1–v0.2)
- Convert one high-level goal into a **task tree** with clear ownership and acceptance criteria.
- Provide a **One Dashboard** for:
  - task status (To Do / In Progress / Blocked / Done),
  - assignments (AI role vs human role),
  - blockers requiring human decision,
  - evaluation results.
- Support **prompt versioning + evaluation** to know whether a prompt change improved outcomes.

### Non-goals (initial)
- Solving code merge conflicts / task interference in depth.
- Full "agentic coding" execution at scale (we may add later, but not required for MVP).
- Perfect generalization across all domains (demo focuses on SaaS app dev tasks).

### Project Scope Clarification (v0.2 Update)

Based on team discussion (2026-01-31), we clarify our scope by leveraging **Vibe Kanban** (an open-source AI coding agent orchestration platform):

**What We Build (Our Focus):**
- **Intelligent task decomposition** — Vibe Kanban lacks this; we provide LLM-powered task splitting
- **Task visualization in Jira** — Deep Jira integration as our key differentiator
- **Human role system** — Enable team collaboration, not just individual developer use
- **Scrum workflow rules** — Sprint context, daily standup reports, human checkpoints

**What We Reuse from Vibe Kanban (Not Building):**
- Task execution & dispatch (multi-agent orchestration framework)
- Git Worktree isolation for parallel development
- Real-time status tracking (WebSocket/SSE)
- Approval workflow infrastructure

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
- **Role** = responsibility/contract (e.g., "Task Decomposer", "QA Evaluator", "Human Architect").
- **Agent** = a runnable worker that performs a role using prompts + tools.
- One role may be backed by **multiple agents** (e.g., parallel decomposers), and one agent may implement multiple roles in early stages.

### 6.3 Blocker
A task state requiring human decision/approval before progress continues (e.g., choosing between 3 design options, approving UI draft).

### 6.4 Acceptance / Evaluation
A mechanism to decide whether a task is "Done":
- human review,
- automated checks (tests, lint, schema validation),
- LLM-as-judge rubric scoring (cross-model evaluation).

### 6.5 Workspace (v0.2 — from Vibe Kanban)
An isolated working environment for task execution:
- Based on **Git Worktree** — each task gets its own branch and working directory
- Enables parallel development without conflicts
- Contains: branch name, container reference, agent working directory

### 6.6 Session (v0.2 — from Vibe Kanban)
An AI Agent conversation thread within a Workspace:
- Tied to a specific executor (Claude Code, Gemini CLI, etc.)
- Tracks conversation history, tool calls, and outputs
- Supports session forking for exploring alternatives

### 6.7 Sprint Context (v0.2 — Scrum Extension)
Scrum workflow context for team collaboration:
- Sprint goal and timeline
- AI-eligible vs human-required stories classification
- Daily standup report generation (AI completed, in progress, blocked)
- Human checkpoint definitions

---

## 7. Product Scope & Roadmap 

> **v0.2 Update:** Roadmap revised to integrate with Vibe Kanban as execution engine.

### 7.1 Phase 1 — Status Visualization + Task Decomposition (MVP)
**What it is:** Intelligent task decomposition with Jira visualization.

**Deliverables**
- Input: user provides a high-level goal in Jira (e.g., "Build a user registration feature").
- Output: system produces:
  - a task tree with LLM-powered decomposition,
  - initial status (mostly To Do),
  - suggested role assignment (AI executor vs human role),
  - per-task acceptance criteria.
- Jira Issue Panel shows AI Workspace status (integrated from Vibe Kanban).
- Basic operations: start/stop AI tasks from Jira.

**Reuse from Vibe Kanban:** Workspace creation, Git Worktree setup, basic status sync.

### 7.2 Phase 2 — Human-in-the-Loop (v0.2)
**What it is:** Human approval and guidance through Jira interface.

**Deliverables**
- Approval request notifications in Jira + Slack integration.
- Approval UI embedded in Jira Issue Panel.
- Code review links to GitHub PR with Jira comment sync.
- New Issue type "AI Blocker" for blocked items requiring human decision.
- Humans can approve/choose options; AI continues with remaining tasks.

**Reuse from Vibe Kanban:** Approval workflow infrastructure, timeout mechanisms.

### 7.3 Phase 3 — Multi-Agent + Role Definition (v0.3)
**What it is:** Assign tasks like a human team — different AI executors and human roles collaborate.

**Deliverables**
- Role catalog with AI executors (Claude Code, Gemini, Codex, etc.) and human roles.
- Role-based task dispatch with autonomy level configuration.
- Parallel execution across Workspaces, with logs and handoffs.
- Sprint context support (AI-eligible vs human-required stories).
- Custom role definition and persistent storage.

---

## 8. Core User Flows (Step-by-Step)

### Flow A: From Goal → Task Tree → Assignments (MVP)
1. **Create Project**
   - user sets project name + short context (repo? stack? constraints?).
2. **Submit Goal**
   - e.g., "Build login UI + backend endpoint".
3. **Task Triage / Estimation**
   - Evaluator agent estimates complexity/duration.
4. **Decompose**
   - If above a threshold, Decomposer splits into subtasks:
     - AI-executable tasks,
     - human-required tasks (blockers),
     - tasks suitable for automated acceptance (tests/checks).
5. **Dispatch**
   - Dispatcher assigns each subtask to a role (human or AI).
6. **Render**
   - UI renders task tree + Kanban columns + owner badges.
7. **(Optional) Iterate**
   - user edits task definitions, acceptance criteria, or role assignments.

### Flow B: Blocker Resolution (v0.2)
1. Task becomes **Blocked (Needs Human Decision)**.
2. System provides **2–3 options** with pros/cons.
3. Human picks an option or provides guidance.
4. Task continues (and dependent tasks unblock).

### Flow C: Prompt Improvement Loop (v0.2+)
1. Prompts are versioned (v1, v2…).
2. Run same seed tasks through prompts.
3. Compare evaluation metrics across versions.
4. Promote the best prompt set to production.

---

## 9. Functional Requirements

### 9.1 Task Creation & CRUD (Create / read / update / delete
- Create / read / update / delete tasks.
- Bulk import: paste PRD (or structured doc) → generate tasks.

### 9.2 Task Decomposition
- Configurable threshold for when to split tasks (time/effort).
- Outputs:
  - subtasks,
  - dependencies,
  - role assignment suggestions,
  - acceptance criteria.

### 9.3 Task Dispatch & Ownership
- Each task must have:
  - **Owner type:** Human / AI,
  - **Role:** from role catalog,
  - **Assignee:** specific agent (optional) or specific person.

### 9.4 Status & Visibility
- Status states (aligned with Vibe Kanban):
  - **To Do** (todo)
  - **In Progress** (inprogress)
  - **In Review** (inreview) — v0.2 addition
  - **Blocked** (blocked)
  - **Done** (done)
  - **Cancelled** (cancelled) — v0.2 addition
  - **Failed (Needs Replan)**

### 9.5 Acceptance & Evaluation
- Each task must include acceptance criteria (required field).
- Evaluation methods supported:
  - rubric-based LLM judge score,
  - simple automated checks (schema validation),
  - manual approval (fallback).

### 9.6 Prompt Management (Internal Tooling)
- Store prompt sets by role.
- Track prompt version used for each task run.
- Support running evaluation suites across prompts.

---

## 10. Non-Functional Requirements (v0.2 Update)

### 10.1 Performance
- Task decomposition response time: < 30 seconds for typical goals.
- Real-time status updates: < 2 second latency via WebSocket/SSE.
- Support concurrent AI tasks: minimum 5 parallel Workspaces.

### 10.2 Security
- **Guard rails:** Prevent AI from exposing sensitive information (API keys, passwords, secrets).
- **Input validation:** Sanitize user prompts before LLM calls.
- **Audit logging:** Track all AI actions and human approvals for compliance.
- **Access control:** Role-based permissions for Jira integration.

### 10.3 Scalability
- Horizontal scaling for backend API services.
- Support multiple Jira projects and teams.
- Efficient task tree storage for large projects (1000+ tasks).

### 10.4 Reliability
- Graceful degradation when AI services are unavailable.
- Task state persistence — no data loss on service restart.
- Timeout handling for long-running AI operations.

---

## 11. Data Model (Draft)

### 11.1 Task Entity (suggested fields)
- `task_id` (uuid)
- `title`
- `description`
- `status` (enum)
- `role` (enum/string)
- `owner_type` (human|ai)
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

### 11.2 Role Catalog (v0.2 — Following Vibe Kanban System)

#### AI Executors (Coding Agents)
Supported AI coding tools for task execution:
- **Claude Code** — Anthropic's coding agent (supports plan mode, approvals)
- **Gemini CLI** — Google's coding agent (supports yolo mode)
- **Codex** — OpenAI's coding agent (supports sandbox mode)
- **Cursor Agent** — Cursor IDE agent
- **Copilot** — GitHub Copilot with MCP server support
- **OpenCode** — Open-source alternative
- **Qwen Code** — Alibaba's coding agent
- **Droid** — Supports autonomy level configuration

#### AI Agent Role Levels
- `junior_developer` — Simple tasks, requires more supervision
- `senior_developer` — Complex tasks, moderate autonomy
- `architect` — Design decisions, high autonomy

#### Autonomy Levels
- `manual` — Human confirmation required for each step
- `supervised` — Confirmation needed at key checkpoints
- `autonomous` — Fully automated execution

#### Human Roles
- `product_owner` — Sets goals and priorities, resolves business blockers
- `scrum_master` — Process management, sprint planning
- `reviewer` — Code review, approval authority
- `architect` — Technical decisions, design approval

#### Role Customization
- Users can define custom roles with specific capabilities
- Role configurations stored persistently (Jira custom fields or backend DB)

---

## 12. UX / UI Requirements

### MVP UI (must look "bright" in demo)
1. **Kanban board** with owner badges (AI/Human) and clear status columns.
2. **Task Tree / Graph view**
   - nodes = tasks,
   - edges = dependencies,
   - color/state = ToDo/InProgress/Blocked/Done.
3. **Blocker Inbox**
   - list of tasks waiting for human decision,
   - each shows options + required context.
4. **Activity Log**
   - per task, show agent actions and outputs.

### Nice-to-have (v0.2–v0.3)
- Gantt-style timeline for estimates.
- Comparison view for prompt versions (A/B view).

---

## 13. Technical Approach (v0.2 — Hybrid Architecture)

### 13.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Jira Cloud                             │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │  Jira Issues    │  │  Forge Plugin   │ ← Management UI  │
│  │  (Task Storage) │  │  (Issue Panel)  │                  │
│  └────────┬────────┘  └────────┬────────┘                  │
└───────────┼────────────────────┼────────────────────────────┘
            │                    │
            ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│                    Backend API Service                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   Task      │  │   Prompt    │  │   Status    │        │
│  │Decomposition│  │  Management │  │    Sync     │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Vibe Kanban (Execution Engine)                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  Workspace  │  │   Session   │  │   Approval  │        │
│  │  Management │  │  Management │  │   Workflow  │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### 13.2 Component Details

- **Jira Forge Plugin:** Management entry point, embedded in Jira Issue Panel
  - Displays AI Workspace status
  - Provides approval UI
  - Triggers task decomposition

- **Backend API Service:** Core business logic
  - Task decomposition (LLM-powered)
  - Prompt management and versioning
  - Jira ↔ Vibe Kanban status synchronization
  - Role and Sprint context management

- **Vibe Kanban:** Execution engine (reused as-is)
  - Multi-agent orchestration
  - Git Worktree isolation
  - Real-time execution tracking

### 13.3 Data Storage Strategy
- **Long-term memory:** Jira Issues (task history, artifacts, comments)
- **Temporary memory:** Backend service (session state, execution context)
- **Execution state:** Vibe Kanban SQLite/PostgreSQL

### 13.4 LLM Provider
- Primary: Google Gemini API (free tier for development)
- Adapter layer for switching to Claude/OpenAI in production

---

## 14. Acceptance Criteria for MVP Demo

A successful demo should show:
1. User submits a high-level goal in Jira.
2. System generates a **task tree** with:
   - clear role assignments (AI executor vs human),
   - acceptance criteria,
   - dependencies.
3. UI shows:
   - Jira Issue Panel with AI task status,
   - at least one **human blocker** task with multiple options,
   - remaining tasks can proceed in parallel via Vibe Kanban.
4. Prompt/eval loop is demonstrated minimally:
   - show prompt version metadata + one comparison score (even if synthetic).

---

## 15. Risks & Open Questions

1. **Role vs agent mapping:** how many agents per role, and when to parallelize?
2. **Decomposition threshold:** what metric (time, story points, uncertainty) triggers splitting?
3. **Evaluation reliability:** LLM-as-judge bias and repeatability; need basic calibration.
4. **Jira API limitations (v0.2):** rate limits, permission constraints for Forge plugins.
5. **Vibe Kanban deployment complexity (v0.2):** Rust compilation, local vs remote execution.
6. **Scope creep:** keep MVP focused on "task decomposition + Jira visualization".

---

## Appendix A — Glossary

- **CRUD:** Create / Read / Update / Delete
- **Kanban:** visual workflow board (To Do → Doing → Done)
- **Scrum:** iterative delivery framework (sprints, roles, ceremonies)
- **Blocker:** a condition preventing a task from progressing without external input
- **Workspace (v0.2):** Git Worktree-based isolated environment for AI task execution
- **Session (v0.2):** AI Agent conversation thread within a Workspace
- **Executor (v0.2):** Specific AI coding tool (Claude Code, Gemini CLI, etc.)
