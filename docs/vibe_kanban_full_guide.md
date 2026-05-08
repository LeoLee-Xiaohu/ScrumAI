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

## 快速命令汇总

```bash
# 完整工作流（推荐）
uv run main.py decompose -t "你的项目目标"
uv run main.py dispatch
uv run main.py evaluate-dispatch
uv run main.py deploy --project-name "你的项目名"

# 分步执行
uv run main.py export-kanban --project-name "你的项目名"
uv run main.py watch-kanban --interval 10
```

## 文件说明

- `decomposed_task.json` - AI 分解的任务列表
- `dispatched_task.json` - 任务分派结果
- `dispatch_evaluation.json` - 分派评估结果
- `kanban_mapping.json` - task_id → Vibe Kanban issue_id 映射（由 export-kanban / deploy 自动生成）
- `mcp_adapter.py` - MCP 模式适配器
