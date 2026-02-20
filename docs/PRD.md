# Scrum AI — Product Requirements Document (PRD)

> **Version:** 0.3
> **Status:** In Progress
> **Last Updated:** Feb 19, 2026

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
- **Role** = responsibility/contract (e.g., "Task Decomposer", "QA Evaluator", "Reviewer").
- **Agent** = a runnable worker that performs a role using prompts + tools + skills.
- **Skill** = a reusable capability or knowledge module that agents can invoke (e.g., "code review", "test generation", "API design").
- One role may be backed by **multiple agents**, and one agent may implement multiple roles in early stages.
- Current roles: 2 AI levels (`Junior Developer`, `Senior Developer`) + 3 human roles (`Product Owner`, `Scrum Master`, `Reviewer`).

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

---

## 7. Product Roadmap

### Phase 1 — Task Decomposition + Role Dispatch (MVP)
Intelligent task decomposition and role-based dispatch.
- User provides a high-level goal
- System produces a task tree with acceptance criteria (implemented: `decompose` CLI)
- Role dispatch evaluates each task on 3 dimensions and assigns roles + autonomy levels (implemented: `dispatch` CLI)
- Issue readiness scoring (implemented: `score` CLI)
- Interactive brainstorm for requirement clarification (implemented: `brainstorm` CLI)

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
- Pipeline automation (brainstorm → score → decompose → dispatch)

---

## 8. Functional Requirements

### 8.1 Task Management
- Create / read / update / delete tasks
- Bulk import from PRD or structured documents

### 8.2 Task Decomposition (implemented)
- LLM-powered task splitting from high-level goal into Epic → Stories → Tasks
- Outputs: subtasks, dependencies, role suggestions, acceptance criteria, execution plan
- CLI: `python main.py decompose -t "goal"` or `python main.py decompose -f goal.md`
- Output format: `decomposed_task.json`

### 8.3 Task Dispatch & Ownership (implemented)
Two-step evaluation framework per task:
- **Step 1 — Delegation scoring**: 3-dimension scoring (Complexity, Risk, Human Judgment; 0-2 each) determines `autonomy_level` (autonomous/supervised/manual) and `owner_type` (ai/human). Adapted from AI Task Delegability Framework (Lubars & Tan, NeurIPS 2019).
- **Step 2 — Role classification**: Task content matched to one of 5 roles, calibrated by few-shot examples from TaskAllocator dataset (Shafiq et al., 2021).
- CLI: `python main.py dispatch -f decomposed_task.json`
- Output format: `dispatched_task.json` (eval-only: task_id + scoring + role + autonomy)

### 8.4 Status & Visibility
- Status states (implemented in code): `todo`, `in_progress`, `blocked`, `done`
- Future additions: `in_review`, `cancelled`, `failed`

### 8.5 Acceptance & Evaluation
- Each task must include acceptance criteria
- Evaluation methods: LLM judge, automated checks, manual approval

### 8.6 Prompt Management
- Store prompt sets by role
- Track prompt version per task run

---

## 9. Non-Functional Requirements

### 9.1 Performance
- Fast task decomposition response
- Real-time status updates
- Support for concurrent AI tasks

### 9.2 Security
- Guard rails to prevent sensitive information exposure
- Input validation and audit logging
- Role-based access control

### 9.3 Scalability
- Support multiple projects and teams
- Efficient storage for large task trees

### 9.4 Reliability
- Graceful degradation when AI services unavailable
- Task state persistence
- Timeout handling for long-running operations

---

## 10. Data Model

### 10.1 Task Entity
- `task_id` (uuid)
- `title`
- `description`
- `status` (enum)
- `role` (string: "Junior Developer", "Senior Developer", "Product Owner", "Scrum Master", "Reviewer")
- `owner_type` ("human" | "ai")
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

### 10.2 Role Catalog

5 pre-defined roles (Title Case format, matching codebase convention):

#### AI Roles
- `Junior Developer` — Simple, well-defined tasks (CRUD, boilerplate, standard patterns). Autonomy: autonomous.
- `Senior Developer` — Complex tasks requiring design decisions, multi-component work, or domain expertise. Autonomy: supervised.

#### Human Roles
- `Product Owner` — Business decisions, priority calls, goal-setting, requirement clarification. Autonomy: manual.
- `Scrum Master` — Process management, sprint planning, team coordination, blocker resolution. Autonomy: manual.
- `Reviewer` — Code review, quality gates, technical approval, design review. Autonomy: manual.

#### Autonomy Levels (assigned by dispatch)
- `autonomous` — Fully automated AI execution, no human oversight needed (total delegation score 0-2)
- `supervised` — AI executes with human review at key checkpoints (total delegation score 3-4)
- `manual` — Human-led execution, AI assists only (total delegation score 5-6)

#### Role Customization (future)
- Users can define custom roles with specific capabilities

---

## 11. UX / UI Requirements

### MVP UI Components
1. **Kanban board** with owner badges and status columns
2. **Task Tree / Graph view** showing dependencies
3. **Blocker Inbox** for items requiring human decision
4. **Activity Log** per task

*Detailed UI specifications to be defined during design phase.*

---

## 12. Acceptance Criteria for MVP Demo

A successful demo should show:
1. User submits a high-level goal via CLI
2. System generates a **task tree** with role assignments, acceptance criteria, and dependencies (`decompose`)
3. System evaluates each task and assigns roles with autonomy levels (`dispatch`)
4. Output clearly distinguishes AI-autonomous, AI-supervised, and human-manual tasks
5. Future: UI displays task status with at least one **human blocker** task

---

## 13. Risks & Open Questions

1. **Dispatch consistency:** LLM-based scoring may vary across runs; calibration via few-shot examples helps but does not guarantee identical results.
2. **Decomposition threshold:** what metric triggers task splitting?
3. **Evaluation reliability:** LLM-as-judge bias and repeatability
4. **Scope creep:** keep MVP focused on core features
5. **Role vs agent mapping:** how many agents per role, and when to parallelize?

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
- **Delegation scoring:** 3-dimension evaluation (Complexity, Risk, Human Judgment) to determine autonomy level
- **Autonomy level:** degree of human oversight (autonomous / supervised / manual)
