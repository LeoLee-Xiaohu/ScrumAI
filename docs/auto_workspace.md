# Auto Workspace for Vibe Kanban Tickets

## 目标

当前从 ScrumAI 导入 tickets 到 Vibe Kanban 后，可以通过 `auto-workspace` 自动创建 workspace 并衔接 PR/review 流程：

1. 监听 Vibe Kanban 中 ScrumAI 导入的 issue ticket。
2. 当 ticket 从 `Todo` / `To do` 移动到 `In Progress` / `In progress` / `In process` 时，自动创建 workspace。
3. workspace 默认使用 `CODEX` executor。
4. workspace 的 repository base branch 默认使用 `origin/main` 对应的 `main` 分支。
5. workspace 创建后立即启动首个 coding session，让 agent 按 ticket 内容执行。
6. 当 code agent 完成开发后，自动创建 GitHub PR，并把 ticket 移动到 `In review`。

本文描述当前已经实现的 `auto-workspace` watcher 及其使用方式。

## 结论

`auto-workspace` 复用现有 `McpClient`，通过 Vibe Kanban MCP 轮询 issue 状态变化；一旦发现目标 issue 进入 `In progress` / `In process`，调用 Vibe Kanban MCP 的 `start_workspace` 工具创建 workspace，并把 issue 与 workspace 关联。随后继续监控该 workspace 的 agent execution；当 execution 成功完成后，推送 workspace 分支到 GitHub、创建 PR，并把 issue 移动到 `In review`。

现有 MCP 工具已经支持这条路径：

- `list_issues`: 获取 issue 状态。
- `get_issue`: 获取 issue title / description。
- `list_repos`: 获取 Vibe Kanban 已注册 repo。
- `start_workspace`: 创建 workspace 并启动 first session。
- `link_workspace_issue`: 将 workspace 与 issue 关联，作为补偿步骤；`409 Conflict` 会按“已关联”处理。
- `list_workspaces`: 用于幂等检查，避免重复创建 workspace。
- `get_execution`: 获取 agent execution 状态，用于判断开发是否完成。
- `update_issue`: 将完成开发的 issue 从进行中状态移动到 `In review`。

GitHub PR 创建不在当前 Vibe Kanban MCP 工具列表中，当前实现使用 GitHub CLI `gh pr create`，复用本机 GitHub 登录态。

## 用户流程

1. 用户运行现有导入流程：

```bash
uv run main.py deploy --project-name "ScrumAI Project" --no-watch
```

2. 用户启动自动 workspace watcher：

```bash
uv run main.py auto-workspace \
  --project-name "ScrumAI Project" \
  --repo-name "ScrumAI" \
  --base-branch main \
  --executor CODEX
```

3. 用户在 Vibe Kanban UI 中把 ticket 从 `Todo` / `To do` 拖到 `In progress` 或 `In process`。
4. watcher 检测到状态变化，自动创建 workspace。
5. Vibe Kanban 中出现 linked workspace，并且 Codex session 开始执行 ticket。
6. watcher 继续轮询 workspace 对应的 `execution_id`。
7. 当 Codex execution 成功结束，watcher 在 workspace 分支上创建 GitHub PR。
8. watcher 将 ticket 从进行中状态移动到 `In review`，并把 PR URL 写入 mapping；后续人工 review / merge。

## 状态触发规则

### Workspace 触发条件

只对满足以下条件的 issue 创建 workspace：

- issue 当前状态是 `In progress`、`In Progress`、`In process` 或 `In Process`。
- issue 之前在 watcher 本地状态中不是进行中状态。
- issue 是 ScrumAI 导入的 ticket，或手动创建的 ticket。
- issue 尚未创建过 workspace。

### ScrumAI ticket 识别

优先使用现有导出标记识别：

- description 包含 `**Task ID:**`
- description 包含 `**Dispatched Role:**`
- description 包含 `**Owner Type:**`
- description 包含 `**Autonomy Level:**`
- description 包含 `**Task Description:**`

兜底规则可以沿用当前 `_looks_like_scrumai_issue_title()`：

- title 形如 `[STORY-001] xxx`

### 手动 ticket 支持

默认处理手动创建的 ticket。watcher 不要求 ScrumAI markers；任何进入 `In progress` / `In process` 且尚未创建 workspace 的 issue 都会触发 workspace 创建。

如果只想处理 ScrumAI 导入 ticket，可使用：

```bash
uv run main.py auto-workspace \
  --project-name "NeckPacMan" \
  --repo-id <repo-id> \
  --scrumai-only
```

建议配合 `--dry-run --once` 先确认会触发哪些 ticket：

```bash
uv run main.py auto-workspace \
  --project-name "NeckPacMan" \
  --repo-id <repo-id> \
  --include-existing-in-progress \
  --dry-run \
  --once
```

手动 ticket 的 title / description 会直接作为 Codex prompt 上下文。手动 ticket 至少应包含目标行为、验收标准、相关文件或复现步骤，以及不应修改的边界。

### 为什么监听 `Todo -> In progress/In process`

`In progress` / `In process` 是用户明确表达“现在可以开始做”的动作，比导入时立即执行更安全：

- 避免所有导入 ticket 同时启动 agent。
- 保留人工排期能力。
- 与现有 `watch-kanban` 的依赖解锁流程兼容：依赖完成后只提升到 `To do`，真正执行仍由用户拖入 `In progress` 控制。

### PR 触发条件

只对满足以下条件的 workspace 创建 PR 并移动 ticket：

- issue 当前状态仍是进行中状态。
- mapping 中已有该 issue 对应的 `workspace_id`，并且已有或可从 session 中恢复 `execution_id`。
- `get_execution(execution_id)` 显示 agent execution 已成功完成。
- workspace branch 存在可提交 / 可推送的代码变更。
- mapping 中还没有 `pull_request.url`。

完成后执行：

```text
In progress/In process -> In review
```

如果 execution 失败、被取消或无代码变更，不移动到 `In review`。当前实现保留在进行中状态，并在日志和 mapping 中记录原因。

## Workspace 创建参数

调用 MCP `start_workspace`：

```json
{
  "name": "[STORY-001] Ticket title",
  "prompt": "由 issue title + description 生成的执行提示",
  "executor": "CODEX",
  "variant": null,
  "repositories": [
    {
      "repo_id": "<resolved_repo_id>",
      "branch": "main"
    }
  ],
  "issue_id": "<issue_id>"
}
```

说明：

- `executor` 使用 Vibe Kanban MCP schema 中的枚举值 `CODEX`，不是小写 `codex`。
- `base branch` 在 Vibe Kanban MCP 的 `start_workspace.repositories[].branch` 中传入。用户语义上的 `origin/main` 应映射为本地分支名 `main`；真正创建 worktree 时由 Vibe Kanban 基于该 repo 的 `origin/main` 同步。
- 如果需要严格确保使用最新 `origin/main`，可以在创建 workspace 前增加可选 preflight：检查 repo 当前 main 是否跟踪 `origin/main`，必要时提示用户在 Vibe Kanban / git 中 fetch。

## Prompt 设计

默认 prompt 不要只丢 issue description，应加上执行边界：

```text
You are working on this Vibe Kanban issue.

Issue:
<title>

Description:
<description>

Requirements:
- Implement the ticket in the linked repository.
- Use the existing code style and tests.
- Keep changes scoped to this ticket.
- Run relevant checks if available.
- Do not merge or delete branches.
```

如果 issue 是 ScrumAI 导出的 ticket，description 中已经包含 acceptance criteria、dispatch role、dependency、reasoning 等信息，Codex 可以直接使用。

## 幂等设计

必须避免用户反复拖动状态时重复创建 workspace，也必须避免 agent 完成后重复创建 PR。当前使用 mapping 文件记录状态，例如：

```json
{
  "project_id": "...",
  "project_name": "ScrumAI Project",
  "repo_id": "...",
  "repo_name": "ScrumAI",
  "executor": "CODEX",
  "base_branch": "main",
  "github": {
    "owner": "oldcai",
    "repo": "ScrumAI",
    "base": "main"
  },
  "issue_to_workspace": {
    "<issue_id>": {
      "workspace_id": "<workspace_id>",
      "session_id": "<session_id>",
      "execution_id": "<execution_id>",
      "workspace_branch": "scrumai/STORY-001-ticket-title",
      "created_at": "2026-05-03T00:00:00Z",
      "pull_request": {
        "url": "https://github.com/oldcai/ScrumAI/pull/123",
        "number": 123,
        "head": "scrumai/STORY-001-ticket-title",
        "base": "main",
        "created_at": "2026-05-03T00:30:00Z"
      },
      "issue_status_after_pr": "In review"
    }
  }
}
```

默认路径：

```text
kanban_workspace_mapping.json
```

幂等检查顺序：

1. 查 mapping 文件中是否已有 `issue_id`。
2. 如 mapping 有记录，调用 `list_workspaces` 或 `list_sessions` 验证 workspace 是否仍存在。
3. 如 workspace 存在，不再创建。
4. 如 workspace 不存在，按配置决定是否重建。默认不自动重建，只打印 warning，避免误重复执行。
5. 创建 PR 前检查 mapping 中是否已有 `pull_request.url`。
6. 如 PR 已存在，不再创建，只确保 issue 位于 `In review`。
7. 如 PR 创建成功但移动 issue 失败，下轮 watcher 应重试移动 issue，不重复创建 PR。

## CLI 方案

启动命令：

```bash
uv run main.py auto-workspace \
  --project-name "ScrumAI Project" \
  --project-id "<optional-project-uuid>" \
  --repo-name "ScrumAI" \
  --repo-id "<optional-repo-uuid>" \
  --base-branch main \
  --executor CODEX \
  --github-repo oldcai/ScrumAI \
  --mapping kanban_workspace_mapping.json \
  --interval 1
```

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--project-name` | `ScrumAI Project` | Vibe Kanban project 名称 |
| `--project-id` | 无 | Vibe Kanban project UUID；当 project name 重复或需要精确指定时优先使用 |
| `--repo-name` | 无，必填 | Vibe Kanban 中已注册的 repository 名称或路径匹配关键字 |
| `--repo-id` | 无 | Vibe Kanban repo UUID；当 repo name 重复时必须使用 |
| `--base-branch` | `main` | workspace 基础分支；对应用户要求的 `origin/main` |
| `--executor` | `CODEX` | Vibe Kanban coding model / executor |
| `--github-repo` | 从 repo remote 推断 | GitHub PR 目标仓库，格式 `owner/repo` |
| `--review-status` | `In review` | agent 完成并创建 PR 后移动到的看板状态 |
| `--pr-base` | 同 `--base-branch` | GitHub PR base branch |
| `--pr-draft` | `false` | 是否创建 draft PR |
| `--skip-pr` | `false` | 只自动创建 workspace，不自动创建 PR / 移动 In review |
| `--mapping` | `kanban_workspace_mapping.json` | issue -> workspace 映射 |
| `--interval` | `1` | 轮询间隔秒数 |
| `--once` | `false` | 扫描一次后退出，方便测试 |
| `--dry-run` | `false` | 只打印将要创建的 workspace，不调用 `start_workspace` |
| `--include-existing-in-progress` | `false` | watcher 启动时已经在 In progress 的 issue 是否也自动创建 workspace |
| `--include-manual-issues` | 已废弃 | 手动 ticket 已默认支持，保留该参数仅兼容旧命令 |
| `--scrumai-only` | `false` | 只处理 ScrumAI 导入 ticket，跳过手动 ticket |

默认不处理 watcher 启动前已处于 `In progress` 的 ticket。原因是无法确认这是新动作还是历史状态，自动执行风险较高。需要补跑时显式加 `--include-existing-in-progress`。

默认同时处理 ScrumAI 导入 ticket 和手动创建 ticket。手动 ticket 没有 `**Task ID:**` 等 export markers 也会被支持；如果某个场景只想处理 ScrumAI 导入 ticket，可显式加 `--scrumai-only`。

### 辅助 CLI

为了避免 project / repo 名称重复导致无法精确选择，提供以下辅助命令。

列出 Vibe Kanban project ID：

```bash
uv run main.py list-kanban-projects
```

输出示例：

```text
Organization: Personal (id=<organization-id>)
  - id=<project-id> name=NeckPacMan
```

列出 Vibe Kanban repo ID：

```bash
uv run main.py list-kanban-repos
```

输出示例：

```text
- id=98afbd2b-9246-4121-8a50-1d7eff091367 name=NeckPacMan path=None default_branch=main
- id=f3ccb0dd-08d6-496d-b216-336aa45c4a3a name=NeckPacMan path=None default_branch=main
```

当出现重复 repo name 时，使用 `--repo-id` 启动：

```bash
uv run main.py auto-workspace \
  --project-name "NeckPacMan" \
  --repo-id 98afbd2b-9246-4121-8a50-1d7eff091367 \
  --base-branch main
```

列出 workspace ID：

```bash
uv run main.py list-kanban-workspaces
```

可选过滤：

```bash
uv run main.py list-kanban-workspaces --name-search NeckPacMan
uv run main.py list-kanban-workspaces --branch main
```

删除 workspace：

```bash
uv run main.py delete-kanban-workspace --workspace-id <workspace-id> --yes
```

可选删除远端 workspace / workspace branches：

```bash
uv run main.py delete-kanban-workspace \
  --workspace-id <workspace-id> \
  --delete-remote \
  --delete-branches \
  --yes
```

删除重复的 Vibe Kanban repo registration：

```bash
uv run main.py delete-kanban-repo --repo-id <repo-id> --yes
```

`delete-kanban-repo` 删除的是 Vibe Kanban 本地登记的 repo 记录，不是 GitHub repository，也不会删除本地源码目录。它通过本地 Vibe Kanban backend API 执行，因为当前 MCP 工具列表只有 `list_repos` / `get_repo`，没有 `delete_repo` 工具。

默认 backend URL：

```text
VIBE_BACKEND_URL or http://127.0.0.1:63861
```

如果 Vibe Kanban backend 端口不同，可以显式传入：

```bash
uv run main.py delete-kanban-repo \
  --repo-id <repo-id> \
  --backend-url http://127.0.0.1:<port> \
  --yes
```

## 模块设计

### `mcp_adapter.py`

当前 MCP client 方法：

```python
def list_repos(self) -> list[dict]: ...

def list_workspaces(
    self,
    archived: bool | None = None,
    name_search: str | None = None,
    branch: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]: ...

def start_workspace(
    self,
    name: str,
    executor: str,
    repo_id: str,
    branch: str,
    prompt: str | None = None,
    issue_id: str | None = None,
    variant: str | None = None,
    model_id: str | None = None,
) -> dict: ...

def link_workspace_issue(self, workspace_id: str, issue_id: str) -> bool: ...

def get_execution(self, execution_id: str) -> dict: ...

def delete_workspace(
    self,
    workspace_id: str,
    delete_remote: bool = False,
    delete_branches: bool = False,
) -> bool: ...

def delete_repo(self, repo_id: str, backend_url: str | None = None) -> tuple[bool, str]: ...
```

`start_workspace` 的返回结构会按 dict 透传保存到 mapping。解析时兼容这些字段：

- `workspace_id`
- `session_id`
- `execution_id`
- `execution_process_id`
- `workspace.id`
- `session.id`
- `execution.id`
- `execution_process.id`

### 新 GitHub/PR helper

当前 PR helper 使用 `gh` CLI：

```python
def create_pull_request(
    repo_path: str,
    github_repo: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    draft: bool = False,
) -> dict: ...
```

实现策略：

1. 在 workspace repo path 下检查 git 状态，确认有 commit 或 branch 与 base 有 diff。
2. 推送 head branch：

```bash
git push -u origin <head_branch>
```

3. 创建 PR：

```bash
gh pr create \
  --repo oldcai/ScrumAI \
  --base main \
  --head <head_branch> \
  --title "<issue title>" \
  --body "<generated PR body>"
```

4. 解析 `gh pr create` 输出的 URL，写入 mapping。

后续如果不希望依赖 `gh`，可改为 GitHub REST API：

```http
POST /repos/{owner}/{repo}/pulls
```

需要 `GITHUB_TOKEN`，并传入 `title`、`head`、`base`、`body`、`draft`。

### Runner

实现文件：

```text
runners/auto_workspace.py
```

职责：

- 解析 project / repo。
- 加载和保存 `kanban_workspace_mapping.json`。
- 维护内存中的上一轮 issue 状态。
- 识别 `Todo -> In progress` 转换。
- 调用 `start_workspace`。
- 轮询 `get_execution`，识别 agent 开发完成。
- 创建 GitHub PR。
- 调用 `update_issue(status="In review")`。
- 将结果写入 mapping。

### `main.py`

已注册 command parser：

```text
auto-workspace
list-kanban-projects
list-kanban-repos
list-kanban-workspaces
delete-kanban-workspace
delete-kanban-repo
```

对应 handler：

```python
def cmd_auto_workspace(args: argparse.Namespace) -> None:
    from runners.auto_workspace import run_auto_workspace
    success = run_auto_workspace(args)
    if not success:
        print("Auto workspace watcher failed.")
        sys.exit(1)
```

## 轮询算法

简化逻辑：

```python
previous_status_by_issue_id = {}

while True:
    issues = client.list_all_issues(project_id)

    for issue in issues:
        if scrumai_only and not is_scrumai_issue(issue):
            continue

        current = normalize_status(issue.status)
        previous = previous_status_by_issue_id.get(issue.id)

        should_start = (
            current in IN_PROGRESS_STATUSES
            and previous not in IN_PROGRESS_STATUSES
            and not has_workspace(issue.id)
        )

        if first_tick and current in IN_PROGRESS_STATUSES and not include_existing:
            should_start = False

        if should_start:
            details = client.get_issue(issue.id)
            prompt = build_prompt(details)
            result = client.start_workspace(
                name=issue.title,
                prompt=prompt,
                executor="CODEX",
                repo_id=repo_id,
                branch="main",
                issue_id=issue.id,
            )
            save_mapping(issue.id, result)

        workspace_record = mapping.issue_to_workspace.get(issue.id)
        should_create_pr = (
            current in IN_PROGRESS_STATUSES
            and workspace_record
            and not workspace_record.get("pull_request", {}).get("url")
            and not skip_pr
        )

        if should_create_pr:
            if not workspace_record.get("execution_id"):
                recover_execution_id_from_sessions(workspace_record)
            execution = client.get_execution(workspace_record["execution_id"])
            if is_execution_success(execution):
                workspace = resolve_workspace(workspace_record["workspace_id"])
                pr = create_pull_request(
                    repo_path=workspace["repo_path"],
                    github_repo=github_repo,
                    head_branch=workspace["branch"],
                    base_branch=pr_base,
                    title=build_pr_title(issue),
                    body=build_pr_body(issue, workspace_record),
                    draft=pr_draft,
                )
                save_pr_mapping(issue.id, pr)
                client.update_issue(issue_id=issue.id, status=review_status)
            elif is_execution_failed(execution):
                mark_execution_failed(issue.id, execution)

        previous_status_by_issue_id[issue.id] = current

    if once:
        break

    sleep(interval)
```

状态归一化：

```python
IN_PROGRESS_STATUSES = {"in progress", "in_process", "in process", "in_progress"}
IN_REVIEW_STATUSES = {"in review", "in_review"}
```

实际触发判断使用归一化后的状态；记录前一状态可以避免 watcher 每轮重复触发。

## PR 创建方案

### 分支策略

workspace 分支保持稳定可预测，避免冲突：

```text
scrumai/<issue-simple-id-or-task-id>-<slug>
```

示例：

```text
scrumai/STORY-001-add-login-validation
```

如果 Vibe Kanban `start_workspace` 自动生成了分支名，优先使用 workspace 返回的 branch。只有无法从 workspace 解析 branch 时，才按上述规则生成。

### PR 标题

默认：

```text
[STORY-001] Ticket title
```

### PR Body

当前 PR body 包含：

```markdown
## Summary

Automated PR generated from Vibe Kanban issue.

## Source Issue

- Vibe Kanban issue: <issue id / simple id>
- Workspace: <workspace id>
- Executor: CODEX
- Base branch: main

## Ticket Description

<issue description>

## Review Notes

- Please review generated changes before merge.
- This PR was created automatically after the agent execution completed successfully.
```

### 创建 PR 后的看板更新顺序

推荐顺序：

1. 确认 execution success。
2. 确认 workspace branch 有 diff。
3. push branch。
4. create PR。
5. 写入 mapping。
6. `update_issue(status="In review")`。

不要先移动到 `In review` 再创建 PR。否则 PR 创建失败时，ticket 会显示为待 review，但没有可 review 的 PR。

## Repo 解析

Vibe Kanban workspace 需要 `repo_id`。当前通过 `list_repos` 解析：

1. 如果用户传 `--repo-id`，直接使用。
2. 否则使用 `--repo-name` 在 `list_repos` 返回结果中匹配：
   - `name` 精确匹配。
   - `path` basename 精确匹配。
   - `path` 包含匹配。
3. 如果匹配多个 repo，报错并打印候选项，要求用户传 `--repo-id`。

也可以直接使用：

```bash
--repo-id <uuid>
```

这样在多 repo 环境下更稳定。

### 重复 repo 处理流程

如果启动时报：

```text
Error: repo name 'NeckPacMan' matched multiple Vibe Kanban repos.
  - id=98afbd2b-9246-4121-8a50-1d7eff091367 name=NeckPacMan path=None
  - id=f3ccb0dd-08d6-496d-b216-336aa45c4a3a name=NeckPacMan path=None
Re-run with --repo-id <uuid> to disambiguate.
```

推荐处理：

1. 先列出 repos：

```bash
uv run main.py list-kanban-repos
```

2. 如果两个 repo 都有效，使用 `--repo-id` 精确启动。

```bash
uv run main.py auto-workspace \
  --project-name "NeckPacMan" \
  --repo-id 98afbd2b-9246-4121-8a50-1d7eff091367 \
  --base-branch main
```

3. 如果其中一个是误创建的重复记录，删除无效 repo registration：

```bash
uv run main.py delete-kanban-repo \
  --repo-id f3ccb0dd-08d6-496d-b216-336aa45c4a3a \
  --yes
```

删除前先确认该 repo 没有关联仍需要保留的 workspace；如不确定，先运行：

```bash
uv run main.py list-kanban-workspaces --name-search NeckPacMan
```

## 与现有 watcher 的关系

现有 `watch-kanban` 负责依赖解锁：

```text
Backlog -> To do
```

`auto-workspace` 负责执行启动：

```text
To do -> In progress/In process -> create workspace + start Codex session -> create GitHub PR -> In review
```

两者可以同时运行：

```bash
uv run main.py watch-kanban --interval 5
uv run main.py auto-workspace --project-name "ScrumAI Project" --repo-name "ScrumAI"
```

当前没有把 `auto-workspace` 塞进 `deploy`。自动执行代码和自动创建 PR 都是有副作用动作，应显式运行 `auto-workspace`。

## 失败处理

### Vibe Kanban API 未运行

`list_repos` / `list_issues` 如果返回 `Failed to connect to VK API`，直接报错：

```text
Vibe Kanban API is not reachable. Start Vibe Kanban with: npx vibe-kanban
```

### Repo 未注册

报错并提示用户先在 Vibe Kanban UI 中添加 git repository。

### Repo 名称重复

如果 `--repo-name` 匹配到多个 repo，`auto-workspace` 会拒绝启动并打印候选 repo IDs。处理方式：

1. 用 `list-kanban-repos` 查看完整 repo 列表。
2. 用 `--repo-id <uuid>` 精确指定。
3. 如果确认某个 repo registration 是重复/无效记录，用 `delete-kanban-repo --repo-id <uuid> --yes` 删除。

### 删除 repo 失败

`delete-kanban-repo` 依赖 Vibe Kanban 本地 backend API。失败常见原因：

- Vibe Kanban 没有运行。
- backend 端口不是默认的 `63861`，需要传 `--backend-url` 或设置 `VIBE_BACKEND_URL`。
- Vibe Kanban backend 当前版本不支持 `DELETE /api/repos/{repo_id}`。
- repo 仍被 workspace 或 project 引用，backend 拒绝删除。

该命令只删除 Vibe Kanban repo registration，不删除 GitHub repo，也不删除本地源码目录。

### Codex executor 不可用

`start_workspace` 失败时打印 MCP 错误，并保持 issue 状态不变。不要自动改回 `Todo`，避免隐藏真实问题。

### Codex model 不兼容

如果 Vibe Kanban UI 或 session log 中出现：

```text
Error: {"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The 'gpt-5.5' model requires a newer version of Codex. Please upgrade to the latest app or CLI and try again."}}
```

不要直接升级到 `vibe-kanban@0.1.44`。该版本当前不可用；`mcp_adapter.py` 仍应固定使用可用的 `vibe-kanban@0.1.43`。

已确认的根因是：`vibe-kanban@0.1.43` 启动的不是系统里的新版 `codex` CLI，而是它自己的 embedded Codex executor。例如日志中会看到：

```text
userAgent: vibe-codex-executor/0.121.0 ... (vibe-codex-executor; 0.1.43)
cliVersion: 0.121.0
codexHome: /Users/leo/.codex
model: gpt-5.5
```

这说明 Vibe 的旧 executor 从 `~/.codex/config.toml` 读取了默认模型 `gpt-5.5`。即使本机 `codex --version` 已经是更新版本，Vibe 仍可能使用旧 executor，因此错误不会通过升级系统 CLI 自动消失。

处理方式：

1. 检查 Codex 全局配置：

```bash
sed -n '1,20p' ~/.codex/config.toml
```

2. 如果第一行是 `model = "gpt-5.5"`，改成旧 executor 可用的模型，例如：

```toml
model = "gpt-5.4"
```

3. 清理失败 workspace 和 mapping，避免 watcher 因旧记录跳过重试：

```bash
uv run main.py delete-kanban-workspace \
  --workspace-id <failed-workspace-id> \
  --delete-remote \
  --delete-branches \
  --yes
```

然后从 `kanban_workspace_mapping.json` 中删除对应 issue 的记录。

4. 重新运行 `auto-workspace`，再检查最新 Vibe process log。成功时应看到类似：

```text
cliVersion: 0.121.0
model: gpt-5.4
```

并且不再出现 `The 'gpt-5.5' model requires a newer version of Codex`。

注意：`auto-workspace --codex-model gpt-5.4` 可以记录期望模型并传给 MCP，但在 `vibe-kanban@0.1.43` 上，实际 Codex executor 仍可能优先读取 `~/.codex/config.toml`。因此遇到该兼容错误时，优先修正 `~/.codex/config.toml`。

### Agent execution 失败

如果 `get_execution` 返回失败、取消或超时：

1. 不创建 PR。
2. 不移动到 `In review`。
3. 在 mapping 中记录 failure：

```json
{
  "execution": {
    "state": "failed",
    "message": "..."
  }
}
```

后续可以增加策略：自动移动到 `Blocked` 或 `Failed`；当前实现保持在进行中状态，让人工判断。

### 没有代码变更

如果 execution 成功但 workspace branch 与 `main` 没有 diff：

1. 不创建 PR。
2. 不移动到 `In review`。
3. 在日志中提示 `No changes detected`。

这通常表示 agent 判断无需改动、失败但状态误报、或修改没有落盘。

### GitHub CLI 未登录

如果使用 `gh pr create`，启动 watcher 前应验证：

```bash
gh auth status
```

未登录时直接失败，并提示：

```bash
gh auth login
```

### GitHub PR 创建成功但移动 In review 失败

mapping 已有 PR URL 时，下轮 watcher 不再创建 PR，只重试：

```python
client.update_issue(issue_id=issue_id, status="In review")
```

### In review 状态不存在

如果 Vibe Kanban project 没有 `In review` 列，`update_issue(status="In review")` 会失败。处理方式：

1. 打印明确错误：需要在 Vibe Kanban UI 中创建 `In review` 状态列。
2. 保留 PR URL。
3. 下轮继续尝试移动状态。

### Workspace 创建成功但 mapping 写入失败

这是最危险的重复执行场景。处理方式：

1. 创建前先写 `creating` 状态到 mapping。
2. `start_workspace` 成功后更新为 `created`。
3. mapping 写失败时打印高优先级错误，watcher 退出。

示例：

```json
{
  "<issue_id>": {
    "state": "creating",
    "started_at": "..."
  }
}
```

### 用户把 ticket 拖回 Todo 再拖到 In progress

如果 mapping 已存在，不创建第二个 workspace。当前不支持自动重建；如果确实需要重新执行，应先删除对应 workspace，并从 `kanban_workspace_mapping.json` 中删除对应 issue 记录，再重新把 ticket 拖入进行中状态。

## 验证步骤

1. 用 `list-kanban-repos` 验证 repo ID；名称重复时用 `--repo-id`。
2. 用 `--dry-run --once` 验证 project / repo / issue 识别。
3. 手动把一个 ScrumAI ticket 拖到 `In progress` / `In process`，验证是否调用 `start_workspace`。
4. 等 agent execution 完成后，验证是否创建 GitHub PR 并移动到 `In review`。
5. 验证重复拖动不会创建第二个 workspace，重复轮询不会创建第二个 PR。

## 验收标准

- 当 ScrumAI ticket 从 `To do` 移动到 `In progress` / `In process` 时，自动创建一个 linked workspace。
- workspace executor 默认是 `CODEX`。
- workspace repository branch 默认是 `main`，对应 `origin/main`。
- 首个 session 使用 issue title + description 作为上下文启动。
- 同一个 issue 多次进入进行中状态不会重复创建 workspace。
- 当 agent execution 成功完成且存在代码变更时，自动创建 GitHub PR。
- PR 创建成功后，ticket 自动从进行中状态移动到 `In review`。
- 同一个 issue 不会重复创建 PR。
- execution 失败、无代码变更、GitHub 认证失败时，不移动到 `In review`。
- 可以列出 Vibe Kanban project/repo/workspace IDs，解决名称重复导致的歧义。
- 可以通过 `delete-kanban-repo --repo-id <uuid> --yes` 删除重复或错误的 Vibe Kanban repo registration。
- `--dry-run --once` 可以安全验证将要执行的动作。
- Vibe Kanban 未运行、repo 未匹配、executor 不可用、`In review` 状态不存在时有明确错误信息。

## 风险与取舍

- Vibe Kanban MCP 当前是轮询式集成，不是事件 webhook，因此会有最多 `interval` 秒延迟。
- `origin/main` 在 MCP 参数中体现为 branch `main`；是否 fetch 最新 origin 由 Vibe Kanban / repo 状态决定。若需要强一致，可以增加 git preflight。
- 自动创建 workspace 会启动真实 coding agent，自动创建 PR 会影响 GitHub repo，因此只在用户明确运行 `auto-workspace` 后启用。
- PR 创建依赖 workspace 分支、GitHub remote、GitHub 认证和 push 权限。当前使用 `gh` CLI；CI / server 场景可改为 GitHub REST API + `GITHUB_TOKEN`。
- `delete-kanban-repo` 使用 Vibe Kanban 本地 backend API，而不是 MCP tool；如果 Vibe Kanban 改端口或 API 路径，需要通过 `--backend-url` 调整或升级适配。
- 是否把成功 execution 直接视为“开发完毕”存在质量风险。更严格的版本可以要求 agent 输出检查结果、测试通过，或增加 reviewer agent gate 后再创建 PR。
- 如果 Vibe Kanban 后续提供状态变更事件或 webhook，应把轮询 watcher 替换为事件驱动，但核心幂等和 mapping 设计不变。
