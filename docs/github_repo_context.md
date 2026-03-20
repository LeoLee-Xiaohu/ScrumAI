# GitHub Repository Context Guide

本文档介绍如何在任务拆解（Task Decomposition）时，将GitHub仓库作为上下文注入给LLM，使AI能够基于现有代码库的结构、模式和架构来拆解问题，而不是凭空生成任务。

## 功能概述

当使用 `decompose` 命令时，通过 `--repo-url` 参数指定GitHub仓库，ScrumAI会自动：

1. **获取仓库结构** - 目录树、文件组织方式
2. **提取关键信息** - 技术栈、依赖配置、主语言
3. **读取核心文件** - README、配置文件、源代码摘要
4. **生成上下文摘要** - 提取类、函数、导入等关键信息
5. **注入给LLM** - 作为prompt的一部分，让AI理解现有代码库

## 工作原理

```
┌─────────────────────────────────────────────────────────────┐
│  User Input                                                 │
│  -t "Add OAuth login" --repo-url owner/repo --branch dev   │
└──────────────────────┬────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  GitHubRepoReader                                           │
│  ├─ 解析URL → owner/repo, branch=develop                   │
│  ├─ GitHub API调用                                         │
│  │   └─ /repos/{owner}/{repo}                              │
│  │   └─ /repos/{owner}/{repo}/contents/{path}             │
│  ├─ 目录遍历 (递归，最大深度3层)                           │
│  └─ 文件内容读取 (base64解码)                              │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  RepoContextGenerator                                       │
│  ├─ 仓库元信息 (stars, language, description)               │
│  ├─ 目录结构树                                              │
│  ├─ 关键文件内容 (README, pyproject.toml, etc.)            │
│  └─ 源代码摘要 (classes, functions, imports)               │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  LLM Prompt Composition                                     │
│  System Prompt: task_decomposition_with_context.md          │
│  └─ {repo_context} + {task_description}                    │
└──────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 基本用法

```bash
# 公开仓库
uv run main.py decompose -t "Add user profile page" --repo-url owner/repo

# 指定分支
uv run main.py decompose -t "Add payment feature" --repo-url owner/repo --branch develop

# 结合文件输入
uv run main.py decompose -f goal.md --repo-url owner/repo
```

### 2. 私有仓库

```bash
# 方式1: 在 .env 文件中配置（推荐）
echo "GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx" >> .env

# 方式2: 直接导出环境变量
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# 现在可以访问私有仓库
uv run main.py decompose -t "Refactor auth module" --repo-url owner/private-repo
```

## 支持的URL格式

| 格式 | 示例 |
|------|------|
| 标准HTTPS | `https://github.com/owner/repo` |
| 带分支 | `https://github.com/owner/repo/tree/main` |
| 带路径 | `https://github.com/owner/repo/tree/main/src` |
| 简化格式 | `owner/repo` |
| SSH格式 | `git@github.com:owner/repo.git` |

## 认证配置

### 获取GitHub Token

1. 访问 [GitHub Settings > Developer settings > Personal access tokens](https://github.com/settings/tokens)
2. 点击 "Generate new token (classic)"
3. 选择以下权限之一：

| 权限 | 适用场景 |
|------|----------|
| `repo` | 完全访问（包括私有仓库） |
| `public_repo` | 仅公开仓库 |

### 环境变量

| 变量名 | 说明 | 优先级 |
|--------|------|--------|
| `GITHUB_TOKEN` | GitHub PAT（推荐） | 高 |
| `GH_TOKEN` | GitHub CLI token | 低 |

```bash
# 在 .env 文件中配置
echo "GITHUB_TOKEN=ghp_xxx" >> .env

# 或直接导出
export GITHUB_TOKEN=ghp_xxx
```

## 分支与版本控制

### 指定分支

```bash
uv run main.py decompose -t "Add feature" --repo-url owner/repo --branch develop
```

### 指定标签

```bash
uv run main.py decompose -t "Add feature" --repo-url owner/repo --branch v1.0.0
```

### 指定Commit

```bash
uv run main.py decompose -t "Fix bug" --repo-url owner/repo --branch abc1234def
```

### 默认行为

- 如果不指定 `--branch`，默认使用仓库的默认分支（通常是 `main`）
- 如果是 fork 仓库，会使用 fork 的默认分支

## 生成的上下文内容

### 示例输出

```
# Repository Context: microsoft/vscode

## Repository Information
| Field | Value |
|-------|-------|
| Full Name | microsoft/vscode |
| Branch | main |
| Description | Visual Studio Code - Open Source IDE |
| Primary Language | TypeScript |
| Stars | 163,000 |
| Forks | 31,000 |
| API Access | Public |

## Directory Structure
├── 📁 .github/
│   └── 📄 workflows/
├── 📁 src/
│   ├── 📄 main.ts
│   ├── 📁 vs/
│   │   ├── 📁 workbench/
│   │   │   └── 📁 browser/
│   │   └── 📁 base/
└── 📄 package.json

## Key Files Content
### 📄 README.md
[README内容摘要]

## Source Code Summary
### 📄 src/main.ts
**Classes:** MainThreadNodePlugin, AbstractNodePlugin
**Functions:** activate(), deactivate(), createPipeline()
```

## 实际示例

### 示例1：前端项目

```bash
uv run main.py decompose \
  -t "Add dark mode toggle to settings page" \
  --repo-url https://github.com/owner/react-app \
  --branch main
```

AI会理解：
- 使用的框架（React）
- 样式方案（CSS Modules / Tailwind / Styled Components）
- 现有的主题/设置实现方式
- 项目结构（components/, styles/, hooks/）

### 示例2：后端API

```bash
uv run main.py decompose \
  -t "Add rate limiting to all API endpoints" \
  --repo-url owner/fastapi-backend \
  --branch develop
```

AI会理解：
- API框架（FastAPI / Express / Django）
- 现有的中间件/装饰器模式
- 数据库模型结构
- 路由组织方式

### 示例3：全栈项目

```bash
uv run main.py decompose \
  -t "Implement real-time notifications" \
  --repo-url owner/fullstack-app \
  --branch main
```

AI会理解：
- 前端技术栈和架构
- 后端技术栈和架构
- 前后端通信方式（REST / GraphQL / WebSocket）
- 数据库方案

## 高级用法

### 聚焦特定路径

通过修改代码可以聚焦特定目录：

```python
from repo_context import RepoContextGenerator

generator = RepoContextGenerator()
context = generator.generate_context(
    repo_url="owner/repo",
    branch="main",
    max_depth=2,
    focus_paths=["src/api", "tests"],  # 只关注这些路径
)
```

### 自定义文件过滤

```python
from repo_context import RepoContextConfig, RepoContextGenerator

config = RepoContextConfig(
    max_file_size=200_000,  # 增大文件大小限制
    include_extensions=[".py", ".md", ".txt"],  # 只包含这些类型
    exclude_patterns=["node_modules", "*.log", "dist/"],
)

generator = RepoContextGenerator(config=config)
context = generator.generate_context("owner/repo")
```

### 纯Python API使用

```python
from repo_context import generate_github_context
from client import get_client
from runners.task import run_decomposition

# 生成仓库上下文
context = generate_github_context(
    repo_url="owner/repo",
    branch="develop",
    token="ghp_xxx",  # 可选
)

# 直接使用
client = get_client()
run_decomposition(
    client,
    task_description="Add user dashboard",
    repo_url="owner/repo",
    branch="develop",
)
```

## 限制与注意事项

### API速率限制

| 类型 | 限制 | 解决方式 |
|------|------|----------|
| 未认证 | 60请求/小时 | 设置 `GITHUB_TOKEN` |
| 已认证 | 5,000请求/小时 | Token认证 |

### 文件大小限制

- 单文件最大：**100KB**
- 可通过 `RepoContextConfig.max_file_size` 调整
- 大文件会被截断或跳过

### 目录深度限制

- 默认最大深度：**3层**
- 可通过 `generate_context(max_depth=N)` 调整
- 过深会触发API限制

### 二进制文件

以下文件类型会被自动过滤：

```
*.exe, *.dll, *.so, *.dylib, *.png, *.jpg, *.gif,
*.pdf, *.zip, *.tar.gz, node_modules/, dist/
```

### API配额消耗

每个仓库上下文生成大约消耗 **10-50次** API请求：

- 1次：获取仓库信息
- 1-10次：目录列表
- 5-40次：文件内容读取

## 故障排除

### 错误：404 Not Found

```
Error: Resource not found: /repos/owner/repo
```

**原因**：仓库不存在或URL格式错误

**解决**：
```bash
# 检查URL格式
# 正确：owner/repo
# 错误：github.com/owner/repo
```

### 错误：403 Forbidden

```
Error: Access denied. Check your GitHub token permissions.
```

**原因**：Token权限不足或仓库是私有的

**解决**：
```bash
# 确认Token有repo权限
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

### 错误：401 Unauthorized

```
Error: Invalid or expired GitHub token.
```

**原因**：Token无效或已过期

**解决**：
1. 检查Token是否正确
2. 重新生成Token

### 错误：Rate Limit Exceeded

**原因**：API请求次数超限

**解决**：
```bash
# 认证后可以提高到5000次/小时
export GITHUB_TOKEN=ghp_xxx
```

### 上下文为空

**表现**：生成的JSON中没有引用任何仓库文件

**原因**：
1. 仓库为空
2. 文件都被过滤了

**解决**：检查仓库内容，或调整 `RepoContextConfig`

## 命令参考

```bash
uv run main.py decompose --help
```

```
usage: main.py decompose [-h] [-f FILE] [-t TASK] [-o OUTPUT]
                          [--repo-url REPO_URL] [--branch BRANCH]

options:
  -f FILE, --file FILE    目标描述文件
  -t TASK, --task TASK    目标描述文本
  -o OUTPUT, --output OUTPUT
                          输出JSON文件 (default: decomposed_task.json)
  --repo-url REPO_URL     GitHub仓库URL
                          支持格式:
                          - https://github.com/owner/repo
                          - owner/repo
                          - git@github.com:owner/repo.git
  --branch BRANCH         分支/标签/commit
                          (default: main 或仓库默认分支)
```

## 最佳实践

1. **选择合适的分支**
   - 使用 `main` 或 `master` 获取最新稳定代码
   - 使用 `develop` 获取开发中代码
   - 使用 PR 分支获取待审查的更改

2. **描述要具体**
   ```bash
   # ✅ 好：具体的业务目标
   -t "Add user profile picture upload with cropping"

   # ❌ 差：过于宽泛
   -t "Add user features"
   ```

3. **先小范围测试**
   ```bash
   # 先测试公开仓库
   uv run main.py decompose -t "..." --repo-url owner/public-repo

   # 确认无误后使用私有仓库
   uv run main.py decompose -t "..." --repo-url owner/private-repo
   ```

4. **关注Token安全**
   - 不要将Token提交到版本控制
   - 使用 `.env` 文件管理
   - 定期轮换Token

5. **合理设置深度**
   ```bash
   # 小型项目：默认深度3
   --repo-url owner/small-repo

   # 大型项目：减少深度避免超时
   --repo-url microsoft/vscode  # 默认3层即可
   ```
