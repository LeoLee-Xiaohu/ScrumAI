"""Role dispatch models for task-to-role assignment.

Two-step decision framework:
  Step 1 (Delegation scoring): 4-dimension scoring → autonomy_level + owner_type
  Step 2 (Role classification): task content → recommended_role

Based on:
  - AI Task Delegability Framework (Lubars & Tan, NeurIPS 2019) — all 4
    scoring dimensions adapted from the paper's factors (Difficulty, Risk,
    Trust, and Motivation reframed as Domain Specificity for agent routing)
  - TaskAllocator dataset (Shafiq et al., JKU 2021) for few-shot calibration

AI roles are domain-based (not seniority-based) because what matters for agent
routing is the task domain (frontend/backend/devops), not experience level —
each domain agent carries a different system prompt with relevant frameworks.
"""

from pydantic import BaseModel, Field
from typing import Literal

from models.scoring import DimensionScore


AI_ROLES = ["Frontend Developer", "Backend Developer", "DevOps"]
HUMAN_ROLES = ["Product Owner", "Scrum Master", "Reviewer"]
ALL_ROLES = AI_ROLES + HUMAN_ROLES


class RoleFitScoring(BaseModel):
    """4-dimension delegation scoring adapted from Lubars & Tan (2019).

      - complexity       ← paper's Difficulty (expertise, effort, creativity)
      - risk             ← paper's Risk (accountability, uncertainty, impact)
      - human_judgment   ← paper's Trust (machine ability, interpretability, value alignment)
      - domain_specificity ← paper's Motivation (reframed: human desire/fit → agent specialization/fit)
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
    domain_specificity: DimensionScore = Field(
        description="Domain expertise required (0=generic, 1=domain-specific, 2=deep expertise)"
    )


class TaskDispatch(BaseModel):
    """Dispatch result for a single task."""

    task_id: str = Field(description="References task_id from decomposed_task.json")
    scoring: RoleFitScoring
    total_score: int = Field(ge=0, le=8, description="Sum of 4 dimension scores")
    recommended_role: str = Field(
        description="One of: Frontend Developer, Backend Developer, DevOps, Product Owner, Scrum Master, Reviewer"
    )
    owner_type: Literal["human", "ai"] = Field(
        description="Derived from total_score: 0-5=ai, 6-8=human"
    )
    autonomy_level: Literal["manual", "supervised", "autonomous"] = Field(
        description="Derived from total_score: 0-2=autonomous, 3-5=supervised, 6-8=manual"
    )
    reasoning: str = Field(description="Brief explanation of role assignment")


class DispatchResult(BaseModel):
    """Complete dispatch result for all tasks."""

    dispatches: list[TaskDispatch]
    summary: str = Field(description="Overall dispatch summary")
