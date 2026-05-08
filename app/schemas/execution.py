from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MissionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskType(str, Enum):
    HTTP_REQUEST = "http_request"
    N8N_WEBHOOK = "n8n_webhook"
    SHELL_COMMAND = "shell_command"
    FILE_WRITE = "file_write"
    LLM_TASK = "llm_task"


class TaskDefinition(BaseModel):
    task_id: str
    title: str
    type: TaskType
    enabled: bool = True
    depends_on: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = None
    max_retries: int | None = None
    continue_on_error: bool = False
    parallel_group: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str
    title: str
    type: TaskType
    status: TaskStatus
    attempt_count: int = 0
    message: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None


class MissionExecutionRequest(BaseModel):
    mission_id: str
    objective: str
    tasks: list[TaskDefinition]
    context: dict[str, Any] = Field(default_factory=dict)


class MissionExecutionResult(BaseModel):
    mission_id: str
    status: MissionStatus
    summary: str
    task_results: list[TaskResult] = Field(default_factory=list)
