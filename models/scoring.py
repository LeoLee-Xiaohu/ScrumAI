"""Issue readiness scoring models matching src/lib/issue-scorer.ts."""

from pydantic import BaseModel, Field


class DimensionScore(BaseModel):
    """Score result for a single scoring dimension.

    Maps to: DimensionScore in src/lib/issue-scorer.ts
    """

    score: int = Field(ge=0, le=2, description="Score from 0 to 2")
    reason: str = Field(description="Reason explaining the score")


class ScoringDimensions(BaseModel):
    """All 5 scoring dimensions for issue readiness.

    Maps to: ScoringDimensions in src/lib/issue-scorer.ts
    """

    runtimeTarget: DimensionScore = Field(
        description="Is the target environment clear? (0-2)"
    )
    deliveryForm: DimensionScore = Field(
        description="Is the delivery format defined? (0-2)"
    )
    controlScheme: DimensionScore = Field(
        description="Is user interaction defined? (0-2)"
    )
    businessRules: DimensionScore = Field(
        description="Are business rules clear? (0-2)"
    )
    acceptanceCriteria: DimensionScore = Field(
        description="Are acceptance criteria complete? (0-2)"
    )


class ScoreResult(BaseModel):
    """Complete score result for an issue.

    Maps to: ScoreResult in src/lib/issue-scorer.ts
    """

    dimensions: ScoringDimensions
    totalScore: int = Field(ge=0, le=10, description="Total score (0-10)")
    summary: str = Field(description="Summary of the overall assessment")
