from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.ai_router import ProviderName, TaskType


class MissionAITask(BaseModel):
    task_id: Optional[str] = None
    title: str = Field(..., min_length=1)
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MissionAIRequest(BaseModel):
    mission_id: Optional[str] = None
    objective: str = Field(..., min_length=1)
    tasks: List[MissionAITask] = Field(default_factory=list)
    preferred_provider: Optional[ProviderName] = None
    stop_on_error: bool = False
    mission_metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskClassificationResult(BaseModel):
    task_id: Optional[str] = None
    title: str
    detected_task_type: TaskType
    confidence: float = 0.0
    reasons: List[str] = Field(default_factory=list)


class MissionAITaskExecutionResult(BaseModel):
    task_id: Optional[str] = None
    title: str
    detected_task_type: TaskType
    selected_agent: str
    selected_provider: ProviderName
    attempted_providers: List[ProviderName] = Field(default_factory=list)
    ok: bool
    response_text: str = ""
    error: Optional[str] = None


class MissionAIResponse(BaseModel):
    ok: bool
    mission_id: Optional[str] = None
    objective: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    results: List[MissionAITaskExecutionResult] = Field(default_factory=list)
