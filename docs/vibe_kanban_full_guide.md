# ScrumAI + Vibe Kanban 完整使用指南

本指南将帮助你从任务拆解到导入 Vibe Kanban 看板的完整流程。

## 准备工作

### 1. 安装依赖

```bash
cd /Users/leo/AI-Projects/ScrumAI
uv sync
```

### 2. 安装 Vibe Kanban（如果尚未安装）

Vibe Kanban 需要在你的本地 Mac 上运行（不是在这个沙盒环境中）。

```bash
# 安装（只需运行一次）
npx -y vibe-kanban@latest

# 之后启动
npx vibe-kanban
```

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

这会生成 `dispatch_evaluation.json` 文件，包含风险评估和建议。

### 步骤 4: 启动 Vibe Kanban

打开终端，运行：

```bash
npx vibe-kanban
```

等待几秒钟，Vibe Kanban 会在浏览器中打开（通常是 http://127.0.0.1:61652）。

**首次使用**：
1. 使用 GitHub 或 Google 账号登录
2. 创建一个新项目（例如 "test-scai"）
3. 添加一些列（To Do, In Progress, Done）

### 步骤 5: 导入任务到 Vibe Kanban

```bash
uv run python main.py export-kanban --use-mcp --project-name "test-scai"
```

**参数说明**：
- `--use-mcp`: 使用 MCP 模式（推荐，需要 Vibe Kanban 运行）
- `--project-name`: Vibe Kanban 中的项目名称

**可选参数**：
- `--decomposed`: 自定义分解任务文件路径（默认 `decomposed_task.json`）
- `--evaluation`: 自定义评估文件路径（默认 `dispatch_evaluation.json`）
- `--no-fallback`: 禁用 SQLite 回退

### 步骤 6: 查看任务

在 Vibe Kanban 浏览器界面中刷新页面，你应该能看到导入的任务。

每个任务包含：
- **标题**: `[STORY-001] 任务名称`
- **描述**: 包含角色、预估时间、验收标准、任务描述等详细信息

## 模式说明

### MCP 模式（推荐）
- 需要 Vibe Kanban 在运行
- 通过 MCP 协议与 Vibe Kanban 通信
- 自动检测登录状态
- 命令：`--use-mcp`

### SQLite 模式（传统）
- 直接写入 Vibe Kanban 数据库
- 需要 Vibe Kanban **未运行**（否则数据库会被锁定）
- 命令：不加 `--use-mcp`

```bash
# SQLite 模式
uv run python main.py export-kanban --project-name "test-scai"
```

## 故障排除

### MCP 模式问题

**"No organizations found"**
- 确保 Vibe Kanban 已登录（浏览器界面中已认证）

**"Project not found"**
- 确保项目已在 Vibe Kanban UI 中创建

### SQLite 模式问题

**"database is locked"**
- Vibe Kanban 正在运行，请先关闭它

**"Permission denied"**
- 数据库文件权限问题，尝试：
  ```bash
  chmod 666 ~/Library/Application\ Support/ai.bloop.vibe-kanban/db.v2.sqlite
  ```

### 常见问题

**Q: 如何更新已存在的任务？**
A: 任务去重基于标题。如果任务已存在，导入时会跳过。

**Q: 可以导入到不同的项目吗？**
A: 可以，使用 `--project-name` 指定项目名称。

**Q: 如何查看导入的任务详情？**
A: 在 Vibe Kanban 中点击任务卡片，描述中包含角色、评估、验收标准等信息。

## 文件说明

- `decomposed_task.json` - AI 分解的任务列表
- `dispatch_evaluation.json` - 分派评估结果
- `mcp_adapter.py` - MCP 模式适配器
- `vibe_kanban_adapter.py` - SQLite 模式适配器
