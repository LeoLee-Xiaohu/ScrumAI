"""Task decomposition models matching the existing main.py output format."""

from pydantic import BaseModel, Field
from typing import Literal


class Epic(BaseModel):
    """High-level goal being decomposed."""

    title: str
    description: str


class Task(BaseModel):
    """A single actionable work item.

    Maps to the task structure in the existing task_decomposition prompt.
    """

    task_id: str = Field(description="Unique identifier (e.g., TASK-001)")
    title: str = Field(description="Clear, action-oriented title")
    description: str = Field(description="What needs to be done")
    status: Literal["todo", "in_progress", "blocked", "done"] = Field(default="todo")
    role: str = Field(
        description='Role type (e.g., "Junior Developer", "Senior Developer")'
    )
    owner_type: Literal["human", "ai"] = Field(default="ai")
    assignee: str = Field(default="", description="Person ID or agent ID")
    estimate_hours: float | None = Field(
        default=None, description="Time estimate in hours"
    )
    story_points: int | None = Field(
        default=None, description="Story point estimate (1, 2, 3, 5, 8)"
    )
    dependencies: list[str] = Field(
        default_factory=list, description="List of task_ids this depends on"
    )
    acceptance_criteria: str = Field(
        description="Clear criteria for task completion"
    )
    blocker_reason: str | None = Field(
        default=None, description="Reason for blocking (only if status is blocked)"
    )
    artifacts: list[str] = Field(
        default_factory=list, description="Links to related docs, code diffs, etc."
    )


class Story(BaseModel):
    """A major deliverable containing multiple tasks."""

    id: str = Field(description="Story identifier (e.g., STORY-001)")
    title: str
    tasks: list[Task]


class ExecutionPhase(BaseModel):
    """A phase in the execution plan."""

    phase: int
    parallel_tasks: list[str] = Field(
        description="Task IDs that can run in parallel in this phase"
    )
    description: str


class ExecutionPlan(BaseModel):
    """Execution plan with phases and critical path."""

    phases: list[ExecutionPhase]
    total_estimated_hours: float
    critical_path: list[str] = Field(
        description="Ordered list of task IDs on the critical path"
    )


class TaskDecompositionResult(BaseModel):
    """Complete result of task decomposition.

    This is the top-level structured output from the task decomposition prompt.
    """

    epic: Epic
    reasoning: str = Field(
        description="Chain of thought analysis explaining the decomposition"
    )
    stories: list[Story]
    execution_plan: ExecutionPlan
