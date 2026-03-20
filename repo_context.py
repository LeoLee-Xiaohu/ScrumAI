"""GitHub repository context reader.

Supports:
- Public and private repositories
- Branch/tag/commit references
- Context-aware content extraction
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class GitHubRepoInfo:
    """Parsed GitHub repository information."""
    owner: str
    repo: str
    branch: str = "main"
    subpath: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class RepoContent:
    """Represents a file or directory in the repository."""
    path: str
    content: str = ""
    is_directory: bool = False
    size: int = 0


@dataclass
class RepoContextConfig:
    """Configuration for repository context generation."""
    max_file_size: int = 100_000
    max_total_tokens: int = 50_000
    include_extensions: list[str] = field(default_factory=lambda: [
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
        ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".xml",
        ".html", ".css", ".scss", ".sql", ".sh", ".bash", ".zsh",
    ])
    exclude_patterns: list[str] = field(default_factory=lambda: [
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        ".pytest_cache", ".mypy_cache", ".tox", "dist", "build",
        ".egg-info", ".DS_Store", "*.pyc", "*.pyo", "*.so", "*.dylib",
        ".env", ".env.local", "*.log", "coverage", ".coverage",
        "*.lock", "package-lock.json", "yarn.lock", "poetry.lock",
    ])


class GitHubRepoReader:
    """Reads GitHub repository contents via API.

    Supports:
    - Public repos without authentication
    - Private repos with GitHub Personal Access Token (PAT)
    - Specific branches, tags, or commits
    - Recursive directory listing and file reading
    """

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        self.session = requests.Session()
        if self.token:
            self.session.headers["Authorization"] = f"token {self.token}"
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "ScrumAI-RepoContext"

    @staticmethod
    def parse_github_url(url: str) -> GitHubRepoInfo | None:
        """Parse GitHub URL into structured info.

        Supports formats:
        - https://github.com/owner/repo
        - https://github.com/owner/repo/tree/branch
        - https://github.com/owner/repo/blob/branch/path/to/file
        - https://github.com/owner/repo/tree/branch/path/to/dir
        - git@github.com:owner/repo.git
        - owner/repo
        """
        patterns = [
            r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/]+))?(?:/.*)?$",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                owner, repo = match.group(1), match.group(2)
                branch = match.group(3) or "main"
                return GitHubRepoInfo(owner=owner, repo=repo, branch=branch)

        parts = url.split("/")
        if len(parts) >= 2:
            return GitHubRepoInfo(owner=parts[0], repo=parts[1])

        return None

    def _api_request(self, endpoint: str) -> dict | list:
        """Make authenticated request to GitHub API."""
        url = f"https://api.github.com{endpoint}"
        response = self.session.get(url, timeout=30)

        if response.status_code == 404:
            raise FileNotFoundError(f"Resource not found: {endpoint}")
        elif response.status_code == 403:
            raise PermissionError(f"Access denied. Check your GitHub token permissions.")
        elif response.status_code == 401:
            raise PermissionError("Invalid or expired GitHub token.")

        response.raise_for_status()
        return response.json()

    def get_default_branch(self, owner: str, repo: str) -> str:
        """Get the default branch of a repository."""
        data = self._api_request(f"/repos/{owner}/{repo}")
        return data.get("default_branch", "main")

    def list_directory(
        self, owner: str, repo: str, path: str = "", ref: str = None
    ) -> list[dict]:
        """List directory contents at a given path and ref."""
        endpoint = f"/repos/{owner}/{repo}/contents/{path}"
        if ref:
            endpoint += f"?ref={ref}"
        data = self._api_request(endpoint)
        return data if isinstance(data, list) else [data]

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str = None
    ) -> str:
        """Get the content of a file (decoded from base64)."""
        import base64

        endpoint = f"/repos/{owner}/{repo}/contents/{path}"
        if ref:
            endpoint += f"?ref={ref}"

        data = self._api_request(endpoint)

        if isinstance(data, list):
            raise IsADirectoryError(f"{path} is a directory")

        if data.get("encoding") == "base64":
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        else:
            content = data.get("content", "")

        return content

    def should_include_file(self, file_info: dict, config: RepoContextConfig) -> bool:
        """Check if a file should be included in context."""
        name = file_info.get("name", "")
        path = file_info.get("path", "")

        for pattern in config.exclude_patterns:
            if pattern.startswith("*."):
                ext = pattern[1:]
                if name.endswith(ext):
                    return False
            elif pattern in path or pattern in name:
                return False

        if file_info.get("size", 0) > config.max_file_size:
            return False

        ext = Path(name).suffix
        if ext not in config.include_extensions and not name.endswith(".md"):
            return False

        return True


class RepoContextGenerator:
    """Generates context from a GitHub repository."""

    def __init__(
        self,
        token: str | None = None,
        config: RepoContextConfig | None = None,
    ):
        self.reader = GitHubRepoReader(token=token)
        self.config = config or RepoContextConfig()

    def generate_context(
        self,
        repo_url: str,
        branch: str | None = None,
        max_depth: int = 3,
        focus_paths: list[str] | None = None,
    ) -> str:
        """Generate a comprehensive context from a GitHub repository.

        Args:
            repo_url: GitHub URL or "owner/repo" format
            branch: Specific branch/tag/commit (overrides URL branch)
            max_depth: Maximum directory depth to explore
            focus_paths: Specific paths to focus on (e.g., ["src", "tests"])

        Returns:
            Formatted context string for LLM consumption
        """
        repo_info = self.reader.parse_github_url(repo_url)
        if not repo_info:
            raise ValueError(f"Invalid GitHub URL: {repo_url}")

        if branch:
            repo_info.branch = branch

        if not repo_info.branch:
            repo_info.branch = self.reader.get_default_branch(
                repo_info.owner, repo_info.repo
            )

        context_parts = []

        context_parts.append(self._generate_header(repo_info))
        context_parts.append(self._generate_repo_info(repo_info))
        context_parts.append(self._generate_directory_structure(repo_info, max_depth))

        if focus_paths:
            for fp in focus_paths:
                context_parts.append(self._generate_path_content(repo_info, fp))
        else:
            context_parts.append(self._generate_core_files(repo_info))

        return "\n\n".join(context_parts)

    def _generate_header(self, repo_info: GitHubRepoInfo) -> str:
        """Generate context header."""
        return f"""# Repository Context: {repo_info.full_name}

This context is extracted from the GitHub repository to provide relevant information for task decomposition.

**Important**: When decomposing tasks, reference this context to ensure tasks align with the existing codebase architecture, patterns, and technologies."""

    def _generate_repo_info(self, repo_info: GitHubRepoInfo) -> str:
        """Generate repository metadata section."""
        try:
            data = self.reader._api_request(
                f"/repos/{repo_info.owner}/{repo_info.repo}"
            )
            description = data.get("description", "No description provided")
            language = data.get("language", "Unknown")
            stars = data.get("stargazers_count", 0)
            forks = data.get("forks_count", 0)

            return f"""## Repository Information

| Field | Value |
|-------|-------|
| Full Name | {repo_info.full_name} |
| Branch | {repo_info.branch} |
| Description | {description} |
| Primary Language | {language} |
| Stars | {stars:,} |
| Forks | {forks:,} |
| API Access | {'Authenticated (private repo)' if self.reader.token else 'Public'} |"""
        except Exception as e:
            logger.warning(f"Failed to fetch repo info: {e}")
            return f"## Repository Information\n- Repository: {repo_info.full_name}\n- Branch: {repo_info.branch}"

    def _generate_directory_structure(
        self, repo_info: GitHubRepoInfo, max_depth: int
    ) -> str:
        """Generate directory tree structure."""
        lines = ["## Directory Structure\n"]

        def build_tree(
            path: str, depth: int, prefix: str = ""
        ) -> Generator[str, None, None]:
            if depth > max_depth:
                return

            try:
                contents = self.reader.list_directory(
                    repo_info.owner, repo_info.repo, path, ref=repo_info.branch
                )
            except Exception as e:
                logger.warning(f"Failed to list {path}: {e}")
                return

            dirs = []
            files = []

            for item in contents:
                if not self.reader.should_include_file(item, self.config):
                    continue

                if item["type"] == "dir":
                    dirs.append(item)
                else:
                    files.append(item)

            items = sorted(dirs, key=lambda x: x["name"]) + sorted(
                files, key=lambda x: x["name"]
            )

            for i, item in enumerate(items):
                is_last = i == len(items) - 1
                current_prefix = "└── " if is_last else "├── "
                next_prefix = "    " if is_last else "│   "

                lines.append(f"{prefix}{current_prefix}{item['name']}")

                if item["type"] == "dir":
                    new_path = f"{path}/{item['name']}" if path else item['name']
                    yield from build_tree(new_path, depth + 1, prefix + next_prefix)

        lines.extend(list(build_tree("", 0)))
        return "\n".join(lines)

    def _generate_core_files(self, repo_info: GitHubRepoInfo) -> str:
        """Generate content from core project files."""
        sections = ["## Key Files Content\n"]

        priority_files = [
            "README.md",
            "CONTRIBUTING.md",
            "ARCHITECTURE.md",
            "pyproject.toml",
            "package.json",
            "requirements.txt",
            "setup.py",
            "Makefile",
            ".github/CONTRIBUTING.md",
        ]

        for file_path in priority_files:
            try:
                content = self.reader.get_file_content(
                    repo_info.owner, repo_info.repo, file_path, ref=repo_info.branch
                )
                truncated = self._truncate_content(content, max_chars=5000)
                sections.append(f"### 📄 {file_path}\n```\n{truncated}\n```\n")
            except (FileNotFoundError, IsADirectoryError):
                pass
            except Exception as e:
                logger.warning(f"Failed to read {file_path}: {e}")

        sections.append("\n## Source Code Summary\n")
        sections.append(self._generate_source_summary(repo_info))

        return "\n".join(sections)

    def _generate_path_content(
        self, repo_info: GitHubRepoInfo, focus_path: str
    ) -> str:
        """Generate content focused on a specific path."""
        sections = [f"## Focused Content: {focus_path}\n"]

        try:
            contents = self.reader.list_directory(
                repo_info.owner, repo_info.repo, focus_path, ref=repo_info.branch
            )

            for item in contents:
                if not self.reader.should_include_file(item, self.config):
                    continue

                if item["type"] == "file":
                    try:
                        content = self.reader.get_file_content(
                            repo_info.owner, repo_info.repo, item["path"], ref=repo_info.branch
                        )
                        truncated = self._truncate_content(content, max_chars=3000)
                        sections.append(f"### 📄 {item['path']}\n```\n{truncated}\n```\n")
                    except Exception as e:
                        logger.warning(f"Failed to read {item['path']}: {e}")

        except Exception as e:
            logger.warning(f"Failed to list focus path {focus_path}: {e}")
            sections.append(f"_Path not found or inaccessible: {focus_path}_")

        return "\n".join(sections)

    def _generate_source_summary(self, repo_info: GitHubRepoInfo) -> str:
        """Generate summary of source code files."""
        sections = []

        source_dirs = ["src", "lib", "app", "packages", "internal"]
        for src_dir in source_dirs:
            try:
                contents = self.reader.list_directory(
                    repo_info.owner, repo_info.repo, src_dir, ref=repo_info.branch
                )

                for item in contents[:10]:
                    if item["type"] == "file" and item["name"].endswith((".py", ".ts", ".js", ".go")):
                        try:
                            content = self.reader.get_file_content(
                                repo_info.owner, repo_info.repo, item["path"], ref=repo_info.branch
                            )
                            summary = self._summarize_code_file(content, item["name"])
                            sections.append(f"### 📄 {item['path']}\n{summary}\n")
                        except Exception:
                            pass
            except Exception:
                pass

        if not sections:
            return "_No source files found in common directories_"

        return "\n".join(sections)

    def _summarize_code_file(self, content: str, filename: str) -> str:
        """Generate a summary of a code file."""
        lines = content.split("\n")

        summary_parts = []
        imports = []
        classes = []
        functions = []

        for line in lines[:150]:
            stripped = line.strip()

            if stripped.startswith(("import ", "from ")):
                imports.append(stripped)
            elif stripped.startswith(("class ", "struct ", "interface ")):
                classes.append(stripped)
            elif stripped.startswith(("def ", "func ", "async def ")):
                functions.append(stripped)

        if imports[:5]:
            summary_parts.append("**Imports:** " + ", ".join(imports[:5]))
            if len(imports) > 5:
                summary_parts[-1] += f" ... (+{len(imports) - 5} more)"

        if classes:
            summary_parts.append("**Classes/Interfaces:** " + "; ".join(classes[:5]))

        if functions:
            summary_parts.append("**Functions:** " + "; ".join(functions[:10]))

        if not summary_parts:
            return self._truncate_content(content, max_chars=300)

        return "\n".join(summary_parts)

    def _truncate_content(self, content: str, max_chars: int) -> str:
        """Truncate content to maximum character limit."""
        if len(content) <= max_chars:
            return content

        lines = content.split("\n")
        truncated = []
        current_len = 0

        for line in lines:
            if current_len + len(line) + 1 > max_chars:
                truncated.append(f"\n... [truncated, {len(lines) - len(truncated)} more lines]")
                break
            truncated.append(line)
            current_len += len(line) + 1

        return "\n".join(truncated)


def generate_github_context(
    repo_url: str,
    branch: str | None = None,
    token: str | None = None,
    max_depth: int = 3,
) -> str:
    """Convenience function to generate GitHub repository context.

    Args:
        repo_url: GitHub repository URL or "owner/repo" format
        branch: Specific branch/tag/commit to read from
        token: GitHub Personal Access Token for private repos
        max_depth: Maximum directory depth to explore

    Returns:
        Formatted context string suitable for LLM consumption

    Examples:
        >>> context = generate_github_context("https://github.com/owner/repo")
        >>> context = generate_github_context("owner/repo", branch="develop")
        >>> context = generate_github_context("github.com/owner/private-repo", token="ghp_xxx")
    """
    generator = RepoContextGenerator(token=token)
    return generator.generate_context(
        repo_url=repo_url,
        branch=branch,
        max_depth=max_depth,
    )
