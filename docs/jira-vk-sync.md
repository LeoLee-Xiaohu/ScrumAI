# Jira ↔ Vibe Kanban Sync

This guide explains how to use `python main.py sync` to keep a Jira project and a
Vibe Kanban (VK) project in lock-step status: brainstorming and decomposition
happen in Jira, execution happens in VK, and progress flows back to Jira so PMs
watching the Jira board see real-time execution status without leaving their
tooling.

## 1. What it does

The sync engine runs a polling loop that mirrors **status changes** between a
Jira project and a Vibe Kanban project in both directions:

- **Jira → VK**: every Jira issue (except `Backlog`) is mirrored into VK as a
  task titled `[<JIRA_KEY>] <summary>`. Title, description, and status all flow
  Jira-to-VK; Jira owns descriptive content.
- **VK → Jira**: when an operator drags a VK card to a new column, the syncer
  transitions the matching Jira issue to the equivalent status. Status only —
  Jira owns titles, descriptions, sub-tasks, labels, etc.

The loop is **idempotent** and **anti-loop**: re-running a tick on unchanged
state is a no-op, and the engine refuses to bounce its own writes back across
the boundary.

### End-to-end workflow this enables

1. PM creates a Jira issue and brainstorms in it (`brainstorm` / `score` /
   `decompose` commands).
2. Sub-tasks are pushed to VK (today via `export-kanban`; status mirroring
   continues afterwards through `sync`).
3. Engineers execute in VK, dragging cards across `To do → In progress → In
   review → Done`.
4. Each VK status change auto-transitions the matching Jira issue.
5. If review reveals more work is needed, reviewers move the Jira issue back to
   `In Progress`; VK picks the work back up because the next tick mirrors the
   Jira move into VK.

## 2. Prerequisites

### Jira

- A Jira Cloud site (`https://yoursite.atlassian.net`).
- A **classic** Atlassian API token (starts with `ATATT`). Scoped tokens
  (`ATCTT` prefix) silently 401 against the Jira REST endpoints unless you
  grant explicit Jira scopes.
  Create one at <https://id.atlassian.com/manage-profile/security/api-tokens>.
- A Jira project whose workflow has the standard `To Do / In Progress / In
  Review / Done` statuses. The transition IDs are currently hard-coded for
  project key `SCRUM` (see [§7](#7-extending-to-other-jira-projects) for how
  to adapt).

### Vibe Kanban

- VK running locally (`npx vibe-kanban`) or accessible via the MCP tunnel.
- A target VK project created in the UI — MCP mode cannot create projects yet.
- Node.js / `npx` available so the engine can spawn the bundled
  `vibe-kanban@latest --mcp` server.

### Python

- Python 3.12+
- Dependencies installed: `uv sync`.

## 3. Configuration

Add the following to your `.env` file (see [`.env.example`](../.env.example)):

```ini
JIRA_BASE_URL=https://yoursite.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=ATATT...
```

LLM keys (`OPENAI_API_KEY` / `GEMINI_API_KEY`) are **not** required for sync —
the engine only talks to the Jira REST API and the VK MCP server.

## 4. CLI usage

```bash
# 30-second polling loop, default project SCRUM ↔ "Initial Project"
uv run python main.py sync

# Single tick — useful for smoke-testing or driving from cron
uv run python main.py sync --once

# Custom interval
uv run python main.py sync --interval 60

# Different Jira project / VK project
uv run python main.py sync \
  --jira-project-key MYPROJ \
  --vk-project-name "My Team Board"
```

`Ctrl+C` (or `kill <pid>`) drains the current tick and exits cleanly.

### Read-only discovery probe

Before pointing the syncer at a new project, you can confirm what statuses,
transitions, and VK projects are visible to your credentials:

```bash
uv run python -m sync.probe
```

The probe never writes to either side. Copy the relevant bits into
[`sync/state_map.py`](../sync/state_map.py) if you need to extend the mapping
beyond project SCRUM.

## 5. Status mapping

### Jira ↔ VK status table

| Jira status   | VK status (canonical / wire) | Notes                                         |
|---------------|------------------------------|-----------------------------------------------|
| `To Do`       | `todo` / `To do`             | Default landing column for new issues         |
| `In Progress` | `inprogress` / `In progress` | Engineer pulled the card                      |
| `In Review`   | `inreview` / `In review`     | Code review / QA                              |
| `Done`        | `done` / `Done`              | Terminal success                              |
| `Cancelled`   | `cancelled` / `Cancelled`    | Terminal failure (no Jira transition wired)   |
| `Backlog`     | *(skipped)*                  | No VK equivalent — issue is **not** mirrored  |

Status mapping is bidirectional and lossless for the five non-Backlog statuses.
A Jira `Backlog` issue is intentionally excluded from VK to keep the board
focused on actionable work.

### A subtle wire-format quirk

Vibe Kanban 0.1.43 expects the **display form** (`'In progress'`) on writes;
passing the lowercase compact form (`'inprogress'`) returns success but
silently no-ops. The engine handles this internally via
`vk_status_to_display()` — callers don't need to know.

### Identity binding

VK doesn't expose an `external_id` column over MCP, so the Jira key is encoded
in the VK title:

```
[SCRUM-26] Implement login page
^^^^^^^^^^
the binding key
```

Locally-created VK tasks (no `[KEY-NN]` prefix) are ignored by the syncer.

## 6. How it works

### Per-tick flow

Each tick runs Jira→VK first, then VK→Jira. This order matters: any new Jira
content lands in VK before the reverse pass reads VK, so the reverse pass sees
fresh state and won't push stale deltas backwards.

```
            ┌────────────────────────────┐
            │ SyncEngine.tick()          │
            └────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
     ┌───────────────┐       ┌───────────────┐
     │ JiraToVkSyncer│       │ VkToJiraSyncer│
     │  • search Jira│       │  • list VK    │
     │  • diff vs VK │       │  • diff Jira  │
     │  • create/    │       │  • transition │
     │    update VK  │       │    Jira       │
     └───────────────┘       └───────────────┘
              │                       │
              └───── MirrorLedger ────┘
                  (90s anti-echo TTL)
```

### Anti-loop strategy

Two layers prevent the engine from chasing its own writes around the loop:

1. **`MirrorLedger`** — every successful write records `(side, jira_key) →
   value` with a 90-second TTL (= 3 ticks at the default 30 s interval). The
   opposite syncer consults the ledger before writing and treats a recent
   matching entry as "this came from us — skip the echo."

2. **Cold-start contract** in `JiraToVkSyncer`: when Jira and VK disagree on
   status with no prior history (process just started), Jira wins. Once a
   baseline exists, the syncer only pushes Jira→VK when **Jira itself moved**
   since the last tick — an unchanged Jira side disagreeing with VK means VK
   was the origin of the change, so JiraToVk stays out of the way and lets
   VkToJira propagate it.

The ledger is in-memory only and rebuilds on restart; any stale state is
re-discovered on the next tick.

### Failure modes the engine guards against

- **Transient VK MCP failure returning empty `list_issues`**: when the SSH
  tunnel to VK dies mid-tick, the MCP server can return a successful
  `{"issues": []}`. Without a guard, the engine would conclude every Jira
  issue is unmirrored and mass-recreate VK tasks. The syncer tracks a
  high-water mark of bound VK tasks; if the previous tick saw N>0 and this one
  sees 0, the create path is aborted with `errors+=1`.
- **Partial create (`update_issue` fails after `create_issue` succeeded)**:
  the Jira-state cache is **not** updated, so the next tick re-enters the
  cold-start path and reconciles the stranded `todo` task.
- **`update_issue` failure**: same — the cache is not updated, so the next
  tick retries the same drift instead of silently treating Jira state as
  already mirrored.

## 7. Extending to other Jira projects

The status **names** are looked up dynamically, but transition **IDs** are
currently hard-coded for project SCRUM in
[`sync/state_map.py`](../sync/state_map.py):

```python
JIRA_TRANSITIONS: Final[dict[str, str]] = {
    JIRA_TODO: "11",
    JIRA_INPROGRESS: "21",
    JIRA_INREVIEW: "31",
    JIRA_DONE: "41",
}
```

To sync a different project, run the discovery probe and update these IDs:

```bash
uv run python -m sync.probe
```

The probe prints transition IDs per source status. If your workflow's
transition IDs differ, edit `JIRA_TRANSITIONS`. Pointing the syncer at a
project with the wrong IDs causes `VkToJiraSyncer` to log a warning and skip
the transition rather than corrupting state — it's a coverage gap, not a
safety bug.

A future enhancement is to discover transition IDs at runtime via the
`/issue/{key}/transitions` REST endpoint per project; this is tracked as a
follow-up.

## 8. Module layout

```
sync/
├── state_map.py     Jira ↔ VK status / transition mapping (Backlog excluded).
├── jira_to_vk.py    Jira → VK syncer. Identity via [KEY-NN] title prefix.
│                    Cold-start contract + bound-count guard live here.
├── vk_to_jira.py    VK → Jira syncer. Status-only.
├── engine.py        Drives both syncers + owns the MirrorLedger.
└── probe.py         Read-only discovery for setup / debugging.
```

| Module          | Responsibility                                                         |
|-----------------|------------------------------------------------------------------------|
| `state_map.py`  | Canonical ↔ display ↔ Jira-name conversions; transition lookup         |
| `jira_to_vk.py` | Reads Jira, indexes VK by `[KEY]` prefix, creates / patches VK tasks   |
| `vk_to_jira.py` | Reads VK, looks up Jira issue, transitions Jira to the matching status |
| `engine.py`     | Tick orchestration, polling loop, signal handling, anti-loop ledger    |
| `probe.py`      | One-shot read-only discovery — never writes                            |

## 9. Tests

The sync module ships with 110+ offline unit tests plus two live-network
smoke scripts:

```bash
# Full unit suite (offline, mocked HTTP + FakeMcpClient)
uv run pytest tests/test_state_map.py tests/test_jira_to_vk.py \
              tests/test_vk_to_jira.py tests/test_engine.py

# Live 4-status round-trip against real Jira + remote VK MCP.
# Not pytest-discovered — invoke directly. Mutates SCRUM-26 (always
# restored to "To Do" via finally block).
uv run python tests/live_smoke.py

# Live end-to-end: creates a fresh Jira ticket, lets sync mirror it to VK,
# round-trips its status, then cleans up.
VIBE_BACKEND_URL=http://127.0.0.1:3000 uv run python tests/live_e2e.py
```

Both live scripts require `.env` (Jira creds) and the SSH tunnel to the VK
backend; see their docstrings for details.

Notable scenarios covered by the unit suite:

- 3-tick anti-loop test (`test_engine_anti_loop_vk_to_jira_then_jira_to_vk_doesnt_bounce`)
  exercises the full Jira-baseline → VK-move → VK→Jira → no-echo flow.
- Bound-count guard regression tests pin the "MCP returned empty" abort path.
- No-cache-on-failure regression tests pin the recovery contract for both
  `_create_vk` and `_update_vk` write paths.

## 10. Troubleshooting

| Symptom                                                          | Likely cause                                                              |
|------------------------------------------------------------------|---------------------------------------------------------------------------|
| `no Vibe Kanban organizations visible`                           | Not signed in to VK — open `http://127.0.0.1:61652` and log in            |
| `VK project '...' not found`                                     | Project name typo, or VK project not yet created in the UI                |
| `VK list returned 0 bound tasks but N were seen last tick`       | Transient MCP connectivity loss — engine aborted to avoid mass-recreate   |
| `No Jira transition for target status 'X'`                       | Workflow has no transition into `X` — configure the transition in Jira    |
| Jira returns 401 on every call                                   | Token is `ATCTT`-scoped; switch to a classic `ATATT` token                |
| Status loop bounces between Jira and VK                          | Should not happen — file a bug with the tick log; ledger TTL is 90 s      |
| `update_issue` succeeded but VK status didn't change             | You're passing a lowercase compact form somewhere; use the display form   |

For deeper debugging, edit `main.py`'s `logging.basicConfig(level=...)` to
`logging.DEBUG`. DEBUG-level logs print every tick, including idle ticks
where no writes happened, plus per-issue diff decisions inside the syncers.

---

**See also:**

- [MCP Adapter Documentation](mcp_adapter.md) — how `mcp_adapter.py` talks to VK
- [Vibe Kanban Integration Guide](vibe_kanban_integration.md) — `export-kanban`
  workflow that bootstraps VK tasks before sync takes over
- [`sync/state_map.py`](../sync/state_map.py) — canonical status/transition tables
- [`sync/engine.py`](../sync/engine.py) — engine + anti-loop ledger
