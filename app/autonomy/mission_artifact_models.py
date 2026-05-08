from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .artifact_models import ArtifactTaskType


class MissionArtifactRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=5000)
    constraints: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    preferred_task_type: ArtifactTaskType | None = None


class MissionArtifactCandidate(BaseModel):
    task_type: ArtifactTaskType
    score: int
    reasons: list[str] = Field(default_factory=list)


class MissionArtifactPlan(BaseModel):
    selected_task_type: ArtifactTaskType
    artifact_name: str
    description: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    candidates: list[MissionArtifactCandidate] = Field(default_factory=list)
    objective: str
    reasoning: list[str] = Field(default_factory=list)


class MissionArtifactRunRecord(BaseModel):
    run_id: str
    status: str
    objective: str
    selected_task_type: str
    artifact_name: str
    created_at: str
    plan: dict[str, Any] = Field(default_factory=dict)
    artifact_result: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
