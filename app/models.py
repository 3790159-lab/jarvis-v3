from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    succeeded = 'succeeded'
    failed = 'failed'


class ExecutorName(str, Enum):
    shell_local = 'shell_local'
    codex_local = 'codex_local'
    codex_cloud = 'codex_cloud'
    noop = 'noop'


class GoalCreate(BaseModel):
    objective: str = Field(min_length=3)
    constraints: dict[str, Any] = Field(default_factory=dict)


class GoalRecord(GoalCreate):
    goal_id: str
    created_at: str


class MissionCreate(BaseModel):
    goal_id: str
    objective: str = Field(min_length=3)
    constraints: dict[str, Any] = Field(default_factory=dict)


class MissionRecord(MissionCreate):
    mission_id: str
    created_at: str


class TaskCreate(BaseModel):
    mission_id: str
    title: str = Field(min_length=3)
    type: str = Field(min_length=2)
    executor: ExecutorName
    working_directory: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)


class TaskRecord(TaskCreate):
    task_id: str
    status: TaskStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class TaskExecutionResult(BaseModel):
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class PlannedTaskSpec(BaseModel):
    title: str
    type: str
    executor: ExecutorName
    working_directory: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)


class IntakeRequest(BaseModel):
    user_request: str = Field(min_length=3)
    project_root: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    goal_constraints: dict[str, Any] = Field(default_factory=dict)
    mission_constraints: dict[str, Any] = Field(default_factory=dict)


class IntakePlan(BaseModel):
    strategy: str
    rationale: str
    goal_objective: str
    mission_objective: str
    tasks: list[PlannedTaskSpec]


class IntakeResponse(BaseModel):
    plan: IntakePlan
    goal: GoalRecord
    mission: MissionRecord
    tasks: list[TaskRecord]


class InterpretationResult(BaseModel):
    mode: Literal['direct_answer', 'execute']
    language: str = 'ru'
    normalized_request: str
    reasoning: str
    direct_answer: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    project_root: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    wait_for_completion: bool = True
    max_wait_seconds: int = Field(default=45, ge=1, le=600)


class ChatResponse(BaseModel):
    mode: Literal['direct_answer', 'execute']
    interpretation: InterpretationResult
    assistant_message: str
    accepted_message: str | None = None
    completed: bool = True
    intake: IntakeResponse | None = None
    tasks: list[TaskRecord] = Field(default_factory=list)
