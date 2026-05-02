"""Test doubles for sync_* tests. Captures MCP/Jira interactions in memory.

These fakes are deliberately minimal — they only implement the methods the
syncers actually call. If you add a new syncer call site, extend the fake
here rather than mocking inside individual tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mcp_adapter import McpIssue


@dataclass
class FakeMcpClient:
    """In-memory stand-in for mcp_adapter.McpClient.

    Project ids are not enforced here — tests pass whatever id they like and
    we treat all issues as belonging to one virtual project. Tests that care
    about the id can inspect `created_calls` / `updated_calls`.
    """

    issues: list[McpIssue] = field(default_factory=list)
    create_should_fail: bool = False
    update_should_fail: bool = False

    # Recorded interactions for assertions
    created_calls: list[dict] = field(default_factory=list)
    updated_calls: list[dict] = field(default_factory=list)

    _next_id: int = 1000

    def list_all_issues(
        self, project_id: str, status: Optional[str] = None, page_size: int = 100
    ) -> list[McpIssue]:
        if status:
            return [i for i in self.issues if i.status.lower() == status.lower()]
        return list(self.issues)

    def list_issues(
        self,
        project_id: str,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[McpIssue]:
        return self.list_all_issues(project_id, status=status, page_size=limit)

    def create_issue(
        self,
        project_id: str,
        title: str,
        description: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> Optional[str]:
        self.created_calls.append(
            {
                "project_id": project_id,
                "title": title,
                "description": description,
                "priority": priority,
            }
        )
        if self.create_should_fail:
            return None
        new_id = f"vk-{self._next_id}"
        self._next_id += 1
        # New issues default to "todo" — matches running VK 0.1.43 behavior.
        self.issues.append(
            McpIssue(
                id=new_id,
                simple_id=str(self._next_id),
                title=title,
                status="todo",
                priority=priority,
            )
        )
        return new_id

    def update_issue(
        self,
        issue_id: str,
        status: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> bool:
        self.updated_calls.append(
            {
                "issue_id": issue_id,
                "status": status,
                "title": title,
                "description": description,
                "priority": priority,
            }
        )
        if self.update_should_fail:
            return False
        for issue in self.issues:
            if issue.id == issue_id:
                if status is not None:
                    issue.status = status.lower()
                if title is not None:
                    issue.title = title
                if priority is not None:
                    issue.priority = priority
                return True
        return False
