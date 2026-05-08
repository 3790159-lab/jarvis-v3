from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .artifact_models import ArtifactTaskType


class GraphMissionSubmitRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=5000)
    constraints: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    graph_kind: str | None = None
    auto_execute: bool = True


class GraphStepRecord(BaseModel):
    step_id: str
    title: str
    objective: str
    task_type: ArtifactTaskType
    depends_on: list[str] = Field(default_factory=list)
    status: str = "pending"
    attempts: int = 0
    constraints: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)


class GraphEventRecord(BaseModel):
    ts: str
    event: str
    step_id: str | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class GraphMissionRecord(BaseModel):
    graph_mission_id: str
    created_at: str
    updated_at: str
    objective: str
    graph_kind: str
    auto_execute: bool = True
    status: str = "planned"
    execution_state: str = "idle"
    can_execute: bool = True
    can_cancel: bool = True
    steps: list[GraphStepRecord] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, Any] = Field(default_factory=dict)
    events: list[GraphEventRecord] = Field(default_factory=list)
