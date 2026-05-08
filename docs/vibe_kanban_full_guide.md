# ScrumAI + Vibe Kanban 完整使用指南

本指南将帮助你从任务拆解到导入 Vibe Kanban 看板的完整流程。

## 准备工作

### 1. 安装依赖

```bash
cd /Users/leo/AI-Projects/ScrumAI
uv sync
```

### 2. 安装 Vibe Kanban

Vibe Kanban 需要在你的本地 Mac 上运行。

```bash
# 启动 Vibe Kanban
npx vibe-kanban
```

等待浏览器打开（通常是 http://127.0.0.1:61652），然后：
1. 使用 GitHub 或 Google 账号登录
2. 创建一个新项目（例如 "ScrumAI Project"）
3. 添加一些列（To Do, In Progress, Done）

## 完整流程

### 步骤 1: 输入产品目标

```bash
uv run main.py decompose -t "你的项目目标描述"
```

例如：
```bash
uv run main.py decompose -t "Build a REST API for managing items with CRUD operations using FastAPI and SQLite"
```
或者
```bash
uv run main.py decompose -f "<your project PRD.md path>"
```
例如：
```bash
uv run main.py decompose -f "../Projects/NeckFlappy/docs/PRD.md"
```

这会生成 `decomposed_task.json` 文件。

### 步骤 2: 分派任务给 AI 或人类

```bash
uv run main.py dispatch
```

这会分析任务并决定哪些由 AI 执行，哪些由人类执行。

### 步骤 3: 评估分派结果

```bash
uv run main.py evaluate-dispatch
```

这会生成 `dispatch_evaluation.json` 文件，用于评估分派准确性。

### 步骤 4: 一键导出 + 启动 Watcher（推荐）

使用 `deploy` 命令，一步完成"导出任务到看板"和"启动依赖监控"两个阶段：

```bash
uv run main.py deploy --project-name "你的项目名"
```

**执行流程**：
1. **Phase 1 – Export**：将任务导出到 Vibe Kanban。已存在的任务会自动识别并加入映射，不会重复创建。
2. **Phase 2 – Watch**：启动依赖监控（Watcher），每隔一定时间扫描看板状态，当某任务的所有前置依赖状态变为 `Done` 时，自动将该任务从 `Backlog` 提升为 `To do`。

**参数说明**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--project-name` | `"ScrumAI Project"` | Vibe Kanban 中的项目名称 |
| `-i` / `--decomposed` | `decomposed_task.json` | 任务拆解文件路径 |
| `-d` / `--dispatched` | `dispatched_task.json` | 任务分派结果文件路径 |
| `--mapping` | `kanban_mapping.json` | task_id → issue_id 映射文件路径 |
| `--interval` | `1` | Watcher 轮询间隔（秒） |
| `--once` | `false` | 只扫描一次后退出，不循环 |
| `--no-watch` | `false` | 只导出，跳过 Watcher 阶段 |

**示例**：

```bash
# 导出并持续监控（默认）
uv run main.py deploy --project-name "NeckPacMan"

# 只导出，不启动 Watcher
uv run main.py deploy --project-name "NeckPacMan" --no-watch

# 导出后只扫描一次依赖关系
uv run main.py deploy --project-name "NeckPacMan" --once

# 自定义轮询间隔（每 10 秒）
uv run main.py deploy --project-name "NeckPacMan" --interval 10
```

### 步骤 4（替代方案）: 分步执行导出和监控

如需单独控制各阶段，可分开执行：

```bash
# 仅导出任务（写入 kanban_mapping.json）
uv run main.py export-kanban --project-name "你的项目名"

# 单独启动 Watcher（依赖 kanban_mapping.json 已存在）
uv run main.py watch-kanban
```

**`watch-kanban` 参数说明**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--mapping` | `kanban_mapping.json` | 映射文件路径（由 export-kanban 生成） |
| `--decomposed` | 映射文件中记录的路径 | 任务拆解文件路径 |
| `--interval` | `5` | 轮询间隔（秒） |
| `--once` | `false` | 只扫描一次后退出 |

### Watcher 工作原理

```
每个轮询周期：
  1. 从 Vibe Kanban 拉取项目内所有 issue 的当前状态
  2. 遍历处于 Backlog 状态的任务
  3. 检查该任务在 decomposed_task.json 中定义的所有依赖项
  4. 若所有依赖项的状态均为 Done → 自动将该任务提升为 To do
  5. 若仍有依赖项未完成 → 保持 Backlog，下一轮继续检查
```

> **注意**：Watcher 在以下情况下会自动退出：
> - 所有任务均已离开 Backlog 状态
> - 使用了 `--once` 参数
> - 按下 `Ctrl+C` 手动中断

### 步骤 5: 查看任务

在 Vibe Kanban 浏览器界面中刷新页面，你应该能看到导入的任务。

每个任务包含：
- **标题**: `[STORY-001] 任务名称`
- **描述**: 包含 dispatched role、owner type、autonomy level、评分、验收标准、任务描述等详细信息

### 步骤 6: 启用 `auto-workspace` 自动开发与自动 PR

`deploy` / `watch-kanban` 只负责把任务导入看板，并根据依赖关系把任务从 `Backlog` 提升到 `To do`。  
真正让任务进入 `In progress` 后自动创建 workspace、启动 Codex、创建 GitHub PR，需要单独启动 `auto-workspace`。

#### 先理解职责分工

- **Vibe Kanban MCP / backend**
  - 监听 issue 状态
  - 创建 workspace
  - 关联 issue 和 workspace
  - 回写 issue 状态到 `In review`
- **本机 `git` / `gh` CLI**
  - 推送 workspace 分支
  - 创建 GitHub PR

这意味着：**PR 创建不是通过 Vibe Kanban MCP 完成的，而是通过本机 `gh pr create` 完成的。**

#### 新环境前置检查

在一台新机器或新的 shell 环境上，启动 `auto-workspace` 前先确认：

```bash
git remote -v
gh auth status
gh repo view owner/repo --json name,viewerPermission,isPrivate
```

如果 `gh auth status` 没登录，先执行：

```bash
gh auth login
```

如果想更稳一点，可以手动验证一次最小 PR 创建权限：

```bash
gh pr create \
  --repo owner/repo \
  --base main \
  --head <existing-branch> \
  --title "test pr permission" \
  --body "test"
```

#### 注册本地 repo 到 Vibe Kanban

`auto-workspace` 只能使用已经注册到 Vibe Kanban 的 repo。推荐先注册本地 git repo，并绑定到 project 默认 repo：

```bash
uv run main.py register-kanban-repo \
  --path /absolute/path/to/NeckFlappy \
  --display-name "NeckFlappy" \
  --default-branch main \
  --project-name "NeckFlappy"
```

如果你的 Vibe Kanban backend 不是默认端口，可以显式指定：

```bash
uv run main.py register-kanban-repo \
  --path /absolute/path/to/NeckFlappy \
  --display-name "NeckFlappy" \
  --default-branch main \
  --project-name "NeckFlappy" \
  --backend-url http://127.0.0.1:61052
```

可用以下命令确认 repo 已注册：

```bash
uv run main.py list-kanban-repos
```

#### 启动 `auto-workspace`

```bash
uv run main.py auto-workspace \
  --project-name "NeckFlappy" \
  --repo-name "NeckFlappy" \
  --github-repo "Leolee-Xiaohu/NeckFlappy" \
  --base-branch main \
  --executor CODEX \
  --backend-url http://127.0.0.1:61052
```

推荐在单独终端中长期运行。随后你只需要在 Vibe Kanban UI 里把任务从 `To do` 拖到 `In progress`。

#### `auto-workspace` 的实际流程

1. 监听 project 内 issue 状态
2. 当 issue 进入 `In progress` 时：
   - 创建 Vibe Kanban workspace
   - 启动 Codex session
3. 等待 execution 完成
4. 在 workspace 对应的 git worktree 中：
   - `git push -u origin <workspace-branch>`
   - `gh pr create`
5. PR 创建成功后，把 issue 从 `In progress` 移到 `In review`

#### base branch 同步行为

为了避免从滞后的本地 `main` 开 feature branch，当前 `auto-workspace` 在创建 workspace 前会先校验 base branch：

1. `git fetch origin <base-branch>`
2. 比较本地 `<base-branch>` 和 `origin/<base-branch>`
3. 如果本地 branch 只是 **behind**，默认会自动 fast-forward
4. 如果本地 branch **ahead** 或 **diverged**，会直接拒绝创建 workspace

默认 `--sync-base-branch` 已开启。  
如果你不希望 watcher 自动修改本地 base branch，可以显式关闭：

```bash
uv run main.py auto-workspace ... --no-sync-base-branch
```

#### 常用参数

| 参数 | 说明 |
|------|------|
| `--project-name` / `--project-id` | 目标 Vibe Kanban project |
| `--repo-name` / `--repo-id` | 目标 repo；repo 名冲突时优先用 `--repo-id` |
| `--github-repo` | GitHub PR 目标仓库，格式 `owner/repo` |
| `--base-branch` | workspace 基线分支，通常是 `main` |
| `--sync-base-branch` / `--no-sync-base-branch` | 是否在创建 workspace 前自动 fast-forward 本地 base branch |
| `--review-status` | PR 创建成功后要回写的看板状态，默认 `In review` |
| `--skip-pr` | 只创建 workspace，不自动创建 PR |
| `--once` | 只扫描一次，适合排查和测试 |
| `--backend-url` | 显式指定 Vibe Kanban backend 地址 |

#### 常见用法

```bash
# 持续运行 watcher
uv run main.py auto-workspace \
  --project-name "NeckFlappy" \
  --repo-name "NeckFlappy" \
  --github-repo "Leolee-Xiaohu/NeckFlappy"

# 只扫描一次，便于排查
uv run main.py auto-workspace \
  --project-name "NeckFlappy" \
  --repo-name "NeckFlappy" \
  --github-repo "Leolee-Xiaohu/NeckFlappy" \
  --once

# 只创建 workspace，不自动 PR
uv run main.py auto-workspace \
  --project-name "NeckFlappy" \
  --repo-name "NeckFlappy" \
  --skip-pr
```

## 故障排除

### "No organizations found"
- 确保 Vibe Kanban 已登录（在浏览器界面中已认证）

### "Project not found"
- 确保项目已在 Vibe Kanban UI 中创建（目前 MCP 模式无法创建项目）

### "No tasks to watch (task_to_issue is empty)"
- 这个问题已修复：export-kanban 现在会将已存在的任务也加入映射
- 如果仍然出现，请确保先运行 `export-kanban` 或 `deploy` 重新生成 `kanban_mapping.json`

### 任务没有显示
- 刷新 Vibe Kanban 页面

### Watcher 立即退出
- 检查 `kanban_mapping.json` 是否存在，若无请先运行 `export-kanban`
- 确认看板中仍有任务处于 `Backlog` 状态（若全部已 `Done`，Watcher 会正常退出）

### `repo '<name>' was not found in Vibe Kanban`

- 先运行 `uv run main.py list-kanban-repos` 确认 repo 是否已注册
- 若未注册，运行 `register-kanban-repo`
- 如果命中多个同名 repo，请改用 `--repo-id`

### `Repo '<name>' does not have a local path in Vibe Kanban`

当前实现会先尝试使用 MCP repo 记录，再自动回退到 backend repo API 补本地路径。  
如果仍报这个错，通常表示 Vibe Kanban 里的 repo registration 本身不完整，或者 backend 读不到该 repo 的本地路径。处理方式：

1. 重新执行 `register-kanban-repo`
2. 再运行 `uv run main.py list-kanban-repos`
3. 如有必要，显式传 `--backend-url`

### `GraphQL: Resource not accessible by personal access token (createPullRequest)`

这通常不是 workspace 创建问题，而是 `gh` 认证上下文有问题。

重点检查：

```bash
gh auth status
gh repo view owner/repo --json name,viewerPermission,isPrivate
```

如果你手动 `gh pr create` 可以成功，但 watcher 仍失败：

1. 停掉旧的 `auto-workspace` 进程
2. 重新启动 watcher，让新进程继承最新 `gh` 登录态
3. 避免在启动 watcher 的 shell 中注入陈旧的 `GH_TOKEN` / `GITHUB_TOKEN`

### PR 经常一创建就冲突

优先怀疑 feature branch 的基线不是最新 `origin/main`。

当前实现已经默认开启 `--sync-base-branch`，会在创建 workspace 前：

1. `fetch origin <base-branch>`
2. 如本地 branch 仅 behind，则自动 fast-forward
3. 如 ahead / diverged，则拒绝创建 workspace

如果仍有冲突，通常说明不是“旧基线”问题，而是 feature branch 创建后 `main` 又继续前进，并且双方修改了同一批文件。这类冲突属于正常 Git 行为，无法完全避免。

## 快速命令汇总

```bash
# 完整工作流（推荐）
uv run main.py decompose -t "你的项目目标"
uv run main.py dispatch
uv run main.py evaluate-dispatch
uv run main.py deploy --project-name "你的项目名"

# 注册 repo 到 Vibe Kanban
uv run main.py register-kanban-repo \
  --path /absolute/path/to/your-repo \
  --display-name "YourRepo" \
  --default-branch main \
  --project-name "你的项目名"

# 启动 auto-workspace
uv run main.py auto-workspace \
  --project-name "你的项目名" \
  --repo-name "YourRepo" \
  --github-repo "owner/YourRepo"

# 分步执行
uv run main.py export-kanban --project-name "你的项目名"
uv run main.py watch-kanban --interval 10
```

## 文件说明

- `decomposed_task.json` - AI 分解的任务列表
- `dispatched_task.json` - 任务分派结果
- `dispatch_evaluation.json` - 分派评估结果
- `kanban_mapping.json` - task_id → Vibe Kanban issue_id 映射（由 export-kanban / deploy 自动生成）
- `kanban_workspace_mapping.json` - issue_id → workspace / PR 映射（由 auto-workspace 自动生成）
- `mcp_adapter.py` - MCP 模式适配器
