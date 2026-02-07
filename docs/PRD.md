# Scrum AI — Product Requirements Document (PRD)

> **Version:** 0.2
> **Status:** In Progress
> **Last Updated:** Feb 2, 2026

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
- **Role** = responsibility/contract (e.g., "Task Decomposer", "QA Evaluator", "Human Architect").
- **Agent** = a runnable worker that performs a role using prompts + tools + skills.
- **Skill** = a reusable capability or knowledge module that agents can invoke (e.g., "code review", "test generation", "API design").
- One role may be backed by **multiple agents**, and one agent may implement multiple roles in early stages.

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

### Phase 1 — Task Decomposition + Visualization (MVP)
Intelligent task decomposition with visual dashboard.
- User provides a high-level goal
- System produces a task tree with role assignments and acceptance criteria
- Dashboard shows task status and AI workspace state

### Phase 2 — Human-in-the-Loop
Human approval and guidance integration.
- Approval notifications and UI
- Code review integration
- Blocker management for items requiring human decision

### Phase 3 — Multi-Agent Collaboration
Team-style task assignment with multiple AI and human roles.
- Role catalog with AI executors and human roles
- Role-based task dispatch with autonomy levels
- Sprint context support

---

## 8. Functional Requirements

### 8.1 Task Management
- Create / read / update / delete tasks
- Bulk import from PRD or structured documents

### 8.2 Task Decomposition
- LLM-powered task splitting with configurable threshold
- Outputs: subtasks, dependencies, role suggestions, acceptance criteria

### 8.3 Task Dispatch & Ownership
- Owner type: Human / AI
- Role assignment from catalog
- Assignee: specific agent or person

### 8.4 Status & Visibility
- Status states: To Do, In Progress, In Review, Blocked, Done, Cancelled, Failed

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

### 10.2 Role Catalog (We should choose whether we need pre-defined role or Agent skill- style like role)

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
1. User submits a high-level goal
2. System generates a **task tree** with role assignments, acceptance criteria, and dependencies
3. UI displays task status with at least one **human blocker** task
4. Basic prompt versioning is demonstrated

---

## 13. Risks & Open Questions

1. **Role vs agent mapping:** how many agents per role, and when to parallelize?
2. **Decomposition threshold:** what metric triggers task splitting?
3. **Evaluation reliability:** LLM-as-judge bias and repeatability
4. **Scope creep:** keep MVP focused on core features

---

## Appendix A — Glossary

- **CRUD:** Create / Read / Update / Delete
- **Kanban:** visual workflow board (To Do → Doing → Done)
- **Scrum:** iterative delivery framework (sprints, roles, ceremonies)
- **Blocker:** a condition preventing a task from progressing without external input
- **Workspace:** isolated environment for AI task execution
- **Session:** AI Agent conversation thread within a Workspace
- **Executor:** AI coding tool that performs tasks
