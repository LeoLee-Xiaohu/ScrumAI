# Scrum AI — Product Requirements Document (PRD)

**Version:** v0.1  
**Project Name:** Scrum AI  

---

## 1. Product Vision (One Sentence)

**Manage AI agents like a human team**: instead of long, messy chat threads, Scrum AI turns an AI workflow into a **visible board + task tree**, where humans step in only at key blockers.

---

## 2. Background & Context

Today’s “coding agents”:
- **Black-box**: agents run ahead with unclear progress and uncertain correctness; or
- **babysitting**: humans must confirm every step, creating high cognitive load.

Scrum AI aims to create a **management-first** interface (board / graph) that makes agent work **transparent, auditable, and asynchronous**.

---

## 3. Target Users

1. **Product & engineering teams using Scrum/Kanban** to ship software.
2. **Teams pushing AI adoption** inside enterprises (need visibility, control, and auditability).
3. Solo builders who want parallel agent execution without losing track of “who is doing what”.


---

## 4. Problems / Pain Points

1. **Information fragmentation:** agent status, logs, and artifacts are scattered → users forget which agent is doing what.
2. **Model specialization complexity:** strongest models differ in strengths; most users can’t orchestrate multiple models well.
3. **Workflow transparency gap:** black-box autonomy vs. babysitting confirmation—no middle ground.

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
- Full “agentic coding” execution at scale (we may add later, but not required for MVP).
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
- **Role** = responsibility/contract (e.g., “Task Decomposer”, “QA Evaluator”, “Human Architect”).
- **Agent** = a runnable worker that performs a role using prompts + tools.
- One role may be backed by **multiple agents** (e.g., parallel decomposers), and one agent may implement multiple roles in early stages.

### 6.3 Blocker
A task state requiring human decision/approval before progress continues (e.g., choosing between 3 design options, approving UI draft).

### 6.4 Acceptance / Evaluation
A mechanism to decide whether a task is “Done”:
- human review,
- automated checks (tests, lint, schema validation),
- LLM-as-judge rubric scoring (cross-model evaluation).

---

## 7. Product Scope & Roadmap 

### 7.1 Phase 5.1 — Status Visualization + Tasks (MVP)
**What it is:** an AI-enhanced To-Do list that turns “one big goal” into smaller tasks with status.

**Deliverables**
- Input: user provides a goal (e.g., “Build a user registration feature”).
- Output: system produces:
  - a task list + tree structure,
  - initial status (mostly To Do),
  - suggested role assignment (AI vs human),
  - per-task acceptance criteria.

### 7.2 Phase 5.2 — Human-in-the-loop Blocker Handling (v0.2)
**What it is:** a Dashboard where blocked items surface clearly.

**Deliverables**
- Visual board + task tree/graph.
- Blockers create a “Human Decision” queue.
- Humans can approve/choose options; other branches continue in parallel.

### 7.3 Phase 5.3 — Multi-Agent Roles (v0.3)
**What it is:** agents become an explicit “team”: different roles collaborate concurrently.

**Deliverables**
- Role catalog (Decomposer, Dispatcher, Evaluator, Developer, Researcher, etc.).
- Parallel execution across roles, with logs and handoffs.
- Stronger “async work” experience (assign tasks and return later).

---

## 8. Core User Flows (Step-by-Step)

### Flow A: From Goal → Task Tree → Assignments (MVP)
1. **Create Project**
   - user sets project name + short context (repo? stack? constraints?).
2. **Submit Goal**
   - e.g., “Build login UI + backend endpoint”.
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
- Status states (initial):
  - **To Do**
  - **In Progress**
  - **Blocked**
  - **Done**
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

## 10. Data Model (Draft)

### 10.1 Task Entity (suggested fields)
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

### 10.2 Role Catalog (initial)
- **AI roles**
  - Task Evaluator (triage + estimation)
  - Task Decomposer (splits into subtasks)
  - Task Dispatcher (assigns roles/owners)
  - QA Evaluator (acceptance scoring)
  - (Future) Developer / Researcher / Designer
- **Human roles**
  - Product Owner / PM (sets goals, prioritizes)
  - Architect / Senior Reviewer (resolves blockers, approves key choices)
  - Maintainer (final review / merge)

---

## 11. UX / UI Requirements

### MVP UI (must look “bright” in demo)
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

## 12. Technical Approach (Proposed)

### 12.1 Architecture (MVP will be discussed)
- **Frontend:** Web app (React/Next.js suggested).
- **Backend:** API service (FastAPI) that:
  - stores tasks,
  - calls LLM,
  - records logs,
  - runs evaluation.
- **LLM Provider:** start with one model (team to pick), but keep an adapter layer for multi-model later.
- **Storage:** lightweight DB (SQLite/Postgres) + file storage for artifacts.

### 12.2 Integration Strategy
- Start as an **API-first** service.
- Optional: wrap as a “plugin” or tool integration later (depends on feasibility and permissions).

### 12.3 Environment Setup (Team Assumption)
- Pick one model + API key management.
- Use a client like **Cherry Studio** (or similar) for quick multi-model API testing during development (not mandatory for product).

---

## 13. Acceptance Criteria for MVP Demo

A successful demo should show:
1. User submits a high-level goal.
2. System generates a **task tree** with:
   - clear role assignments,
   - acceptance criteria,
   - dependencies.
3. UI shows:
   - Kanban + task graph,
   - at least one **human blocker** task with multiple options,
   - remaining tasks can proceed in parallel (simulated is OK).
4. Prompt/eval loop is demonstrated minimally:
   - show prompt version metadata + one comparison score (even if synthetic).

---

## 14. Risks & Open Questions

1. **Role vs agent mapping:** how many agents per role, and when to parallelize?
2. **Decomposition threshold:** what metric (time, story points, uncertainty) triggers splitting?
3. **Evaluation reliability:** LLM-as-judge bias and repeatability; need basic calibration.
4. **Plugin permissions:** feasibility depends on platform constraints.
5. **Scope creep:** keep MVP focused on “task split + dispatch + visibility”.

---

## Appendix A — Glossary

- **CRUD:** Create / Read / Update / Delete
- **Kanban:** visual workflow board (To Do → Doing → Done)
- **Scrum:** iterative delivery framework (sprints, roles, ceremonies)
- **Blocker:** a condition preventing a task from progressing without external input
