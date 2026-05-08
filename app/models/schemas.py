from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


TaskStatus = Literal["queued", "running", "completed", "failed"]
MissionStatus = Literal["draft", "running", "completed", "failed"]


class GoalRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=5000)
    constraints: dict[str, Any] = Field(default_factory=dict)


class TaskRecord(BaseModel):
    task_id: str
    goal_id: str
    task_type: str
    status: TaskStatus = "queued"
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class SupervisorResponse(BaseModel):
    success: bool
    summary: str
    goal_id: str
    mission_id: str
    objective: str
    strategy: str
    status: MissionStatus
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str


class MissionReadResponse(BaseModel):
    found: bool
    mission: dict[str, Any] | None = None
