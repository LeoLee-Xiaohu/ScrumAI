"""Brainstorm models matching src/types/brainstorm.ts and src/lib/brainstorm-prompts.ts."""

from pydantic import BaseModel, Field
from typing import Literal


class BrainstormOption(BaseModel):
    """A single brainstorm option presented to the user.

    Maps to: BrainstormOption in src/types/brainstorm.ts
    """

    label: str = Field(description="Short label (5-10 words max)")
    description: str = Field(description="Brief explanation (1-2 sentences)")
    value: str = Field(description="Unique identifier in snake_case")


class BrainstormScoring(BaseModel):
    """Requirement clarity scoring across 4 dimensions.

    Maps to: BrainstormScoring in src/types/brainstorm.ts
    """

    total: int = Field(ge=0, le=10, description="Total score (0-10)")
    taskGoal: int = Field(ge=0, le=3, description="Is the core intent/goal clear? (0-3)")
    completionCriteria: int = Field(
        ge=0, le=3, description="Are expected results/outcomes defined? (0-3)"
    )
    scope: int = Field(
        ge=0, le=2, description="Are boundaries clear? (0-2)"
    )
    constraints: int = Field(
        ge=0, le=2, description="Are technical constraints identified? (0-2)"
    )
    lowScoreDimensions: list[str] = Field(
        default_factory=list,
        description="Dimension names that need improvement (score < 50% of max)",
    )


class BrainstormSummary(BaseModel):
    """Summary of a completed brainstorming session.

    Maps to: BrainstormSummary in src/lib/brainstorm-prompts.ts
    """

    taskOverview: str
    background: str
    coreFeatures: list[str]
    technicalRequirements: list[str]
    testingPlan: str
    successCriteria: list[str]


class BrainstormResponse(BaseModel):
    """AI response during a brainstorm session.

    Maps to: BrainstormResponse in src/lib/brainstorm-prompts.ts
    This is the JSON format the LLM must return.
    """

    phase: Literal[1, 2, 3, 4] = Field(description="Current brainstorming phase (1-4)")
    question: str = Field(description="The question being asked")
    options: list[BrainstormOption] = Field(
        description="3-4 suggested options (system adds 'Other' automatically)"
    )
    context: str | None = Field(
        default=None, description="Brief context or reasoning for the question"
    )
    isComplete: bool | None = Field(
        default=None, description="Whether the brainstorming is complete"
    )
    generatedPrompt: str | None = Field(
        default=None, description="Generated prompt (only when isComplete is true)"
    )
    summary: BrainstormSummary | None = Field(
        default=None, description="Summary of the brainstorming session"
    )
    scoring: BrainstormScoring | None = Field(
        default=None, description="Requirement clarity scoring"
    )
