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
uv run python main.py decompose -t "你的项目目标描述"
```

例如：
```bash
uv run python main.py decompose -t "Build a REST API for managing items with CRUD operations using FastAPI and SQLite"
```

这会生成 `decomposed_task.json` 文件。

### 步骤 2: 分派任务给 AI 或人类

```bash
uv run python main.py dispatch
```

这会分析任务并决定哪些由 AI 执行，哪些由人类执行。

### 步骤 3: 评估分派结果

```bash
uv run python main.py evaluate-dispatch
```

这会生成 `dispatch_evaluation.json` 文件，用于评估分派准确性。

### 步骤 4: 导入任务到 Vibe Kanban

确保 Vibe Kanban 正在运行，然后执行：

```bash
uv run python main.py export-kanban --project-name "test-scai"
```

**参数说明**：
- `--project-name`: Vibe Kanban 中的项目名称（默认 "ScrumAI Project"）
- `-i` / `--decomposed`: 自定义分解任务文件路径（默认 `decomposed_task.json`）
- `-d` / `--dispatched`: 自定义分派结果文件路径（默认 `dispatched_task.json`）

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

### 任务没有显示
- 刷新 Vibe Kanban 页面

## 快速命令汇总

```bash
# 完整工作流
uv run python main.py decompose -t "你的项目目标"
uv run python main.py dispatch
uv run python main.py evaluate-dispatch
uv run python main.py export-kanban --project-name "你的项目名"
```

## 文件说明

- `decomposed_task.json` - AI 分解的任务列表
- `dispatched_task.json` - 任务分派结果
- `dispatch_evaluation.json` - 分派评估结果
- `mcp_adapter.py` - MCP 模式适配器
