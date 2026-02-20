"""Role dispatch models for task-to-role assignment.

Two-step decision framework:
  Step 1 (Delegation scoring): 3-dimension scoring → autonomy_level + owner_type
  Step 2 (Role classification): task content → recommended_role

Based on:
  - AI Task Delegability Framework (Lubars & Tan, NeurIPS 2019)
  - TaskAllocator dataset (Shafiq et al., JKU 2021) for few-shot calibration
"""

from pydantic import BaseModel, Field
from typing import Literal

from models.scoring import DimensionScore


AI_ROLES = ["Junior Developer", "Senior Developer"]
HUMAN_ROLES = ["Product Owner", "Scrum Master", "Reviewer"]
ALL_ROLES = AI_ROLES + HUMAN_ROLES


class RoleFitScoring(BaseModel):
    """3-dimension delegation scoring adapted from Lubars & Tan (2019).

    Dimensions map to the paper's factors:
      - complexity  ← Difficulty (expertise, effort, creativity)
      - risk        ← Risk (accountability, uncertainty, impact)
      - human_judgment ← Trust (machine ability, interpretability, value alignment)
    """

    complexity: DimensionScore = Field(
        description="Technical complexity (0=routine, 1=moderate, 2=architectural)"
    )
    risk: DimensionScore = Field(
        description="Impact of failure (0=low/reversible, 1=moderate, 2=high/irreversible)"
    )
    human_judgment: DimensionScore = Field(
        description="Human oversight needed (0=none, 1=checkpoints, 2=continuous)"
    )


class TaskDispatch(BaseModel):
    """Dispatch result for a single task."""

    task_id: str = Field(description="References task_id from decomposed_task.json")
    scoring: RoleFitScoring
    total_score: int = Field(ge=0, le=6, description="Sum of 3 dimension scores")
    recommended_role: str = Field(
        description="One of: Junior Developer, Senior Developer, Product Owner, Scrum Master, Reviewer"
    )
    owner_type: Literal["human", "ai"] = Field(
        description="Derived from total_score: 0-4=ai, 5-6=human"
    )
    autonomy_level: Literal["manual", "supervised", "autonomous"] = Field(
        description="Derived from total_score: 0-2=autonomous, 3-4=supervised, 5-6=manual"
    )
    reasoning: str = Field(description="Brief explanation of role assignment")


class DispatchResult(BaseModel):
    """Complete dispatch result for all tasks."""

    dispatches: list[TaskDispatch]
    summary: str = Field(description="Overall dispatch summary")
