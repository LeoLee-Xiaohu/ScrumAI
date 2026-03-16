"""Dispatch evaluation models for assessing role assignment accuracy.

Evaluates the quality of AI-generated role dispatches by comparing them to
original human assignments and validating scoring framework consistency.
"""

from pydantic import BaseModel, Field
from typing import Literal


class OwnerTypeMismatch(BaseModel):
    """A task where dispatched owner_type differs from original."""

    task_id: str
    original_owner: Literal["human", "ai"]
    dispatched_owner: Literal["human", "ai"]
    issue: str = Field(description="Explanation of the mismatch")
    severity: Literal["low", "medium", "high"] = Field(
        description="Impact severity of the mismatch"
    )


class OwnerTypeAnalysis(BaseModel):
    """Analysis of human vs. AI assignment accuracy."""

    matches: int = Field(description="Number of tasks with matching owner_type")
    mismatches: int = Field(description="Number of tasks with different owner_type")
    false_ai_assignments: list[OwnerTypeMismatch] = Field(
        description="Tasks incorrectly assigned to AI (should be human)"
    )
    false_human_assignments: list[OwnerTypeMismatch] = Field(
        description="Tasks incorrectly assigned to human (could be AI)"
    )


class RoleIssue(BaseModel):
    """A task with questionable role assignment."""

    task_id: str
    dispatched_role: str
    suggested_role: str
    reason: str = Field(description="Why the suggested role is more appropriate")


class RoleAnalysis(BaseModel):
    """Analysis of role assignment precision."""

    correct_assignments: int
    incorrect_assignments: int
    confusion_matrix: dict[str, int] = Field(
        description="Role confusion patterns (e.g., Junior_to_Senior: 2)"
    )
    notable_issues: list[RoleIssue] = Field(
        description="Tasks with questionable role assignments"
    )


class DimensionScoreMismatch(BaseModel):
    """A dimension score that doesn't align with task characteristics."""

    task_id: str
    dimension: Literal["complexity", "risk", "human_judgment", "domain_specificity"]
    given_score: int = Field(ge=0, le=2)
    suggested_score: int = Field(ge=0, le=2)
    reason: str = Field(description="Why the suggested score is more appropriate")


class DimensionScoreAnalysis(BaseModel):
    """Analysis of 4-dimension scoring accuracy."""

    aligned_scores: int = Field(
        description="Number of dimension scores aligned with task characteristics"
    )
    misaligned_scores: int = Field(description="Number of questionable scores")
    over_scored_tasks: list[DimensionScoreMismatch] = Field(
        description="Tasks scored higher than warranted"
    )
    under_scored_tasks: list[DimensionScoreMismatch] = Field(
        description="Tasks scored lower than warranted"
    )


class AutonomyMappingIssue(BaseModel):
    """A task where autonomy level doesn't follow the mapping rules."""

    task_id: str
    total_score: int = Field(ge=0, le=8)
    expected_autonomy: Literal["autonomous", "supervised", "manual"]
    actual_autonomy: Literal["autonomous", "supervised", "manual"]
    expected_owner: Literal["human", "ai"]
    actual_owner: Literal["human", "ai"]


class ReasoningIssue(BaseModel):
    """A task with poor reasoning quality."""

    task_id: str
    reasoning: str = Field(description="The problematic reasoning")
    improvement: str = Field(description="How to improve the reasoning")


class ReasoningQualityReport(BaseModel):
    """Assessment of reasoning quality across all tasks."""

    poor_reasoning: list[ReasoningIssue] = Field(
        description="Tasks with inadequate reasoning"
    )
    good_reasoning: list[str] = Field(
        description="Task IDs with exemplary reasoning"
    )


class OverallMetrics(BaseModel):
    """Summary metrics for the evaluation."""

    total_tasks: int
    owner_type_accuracy: float = Field(
        ge=0, le=100, description="% of tasks with correct human/AI assignment"
    )
    role_precision: float = Field(
        ge=0, le=100, description="% of tasks with correct role assignment"
    )
    dimension_alignment: float = Field(
        ge=0, le=100, description="% of dimension scores aligned with task characteristics"
    )
    autonomy_mapping_correctness: float = Field(
        ge=0, le=100, description="% of tasks with correct autonomy level mapping"
    )
    avg_reasoning_quality: float = Field(
        ge=1, le=3, description="Average reasoning quality (1=poor, 3=good)"
    )


class DispatchEvaluationResult(BaseModel):
    """Complete evaluation result for dispatch accuracy."""

    overall_metrics: OverallMetrics
    owner_type_analysis: OwnerTypeAnalysis
    role_analysis: RoleAnalysis
    dimension_score_analysis: DimensionScoreAnalysis
    autonomy_mapping_issues: list[AutonomyMappingIssue]
    reasoning_quality_report: ReasoningQualityReport
    recommendations: list[str] = Field(
        description="Actionable recommendations for improving the dispatch system"
    )
    summary: str = Field(
        description="2-3 sentence summary of the evaluation findings"
    )
