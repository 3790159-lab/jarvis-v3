from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .artifact_models import ArtifactTaskType


class RuntimeBridgeSubmitRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=5000)
    constraints: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    preferred_task_type: ArtifactTaskType | None = None
    auto_execute: bool = True


class RuntimeBridgeGoalRecord(BaseModel):
    goal_id: str
    created_at: str
    updated_at: str
    objective: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    preferred_task_type: str | None = None
    auto_execute: bool = True
    latest_mission_id: str | None = None
    status: str = "created"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeBridgeMissionRecord(BaseModel):
    mission_id: str
    goal_id: str
    created_at: str
    updated_at: str
    status: str
    mode: str = "auto_execute"
    objective: str
    selected_task_type: str | None = None
    artifact_name: str | None = None
    planning_state: str = "created"
    execution_state: str = "not_started"
    can_execute: bool = False
    can_cancel: bool = True
    classification: dict[str, Any] = Field(default_factory=dict)
    execution: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, Any] = Field(default_factory=dict)
