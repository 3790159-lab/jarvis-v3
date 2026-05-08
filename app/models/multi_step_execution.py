from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.ai_router import TaskType


class ExecutionStep(BaseModel):
    step_id: str
    title: str
    description: str = ""
    task_type: TaskType
    preferred_provider: Optional[str] = None
    status: str = "pending"
    response_text: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MultiStepExecutionRequest(BaseModel):
    title: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    description: str = ""
    use_memory: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MultiStepExecutionResponse(BaseModel):
    ok: bool
    mission_id: str
    objective: str
    steps: List[ExecutionStep] = Field(default_factory=list)
    final_summary: str = ""
    memory_written: bool = False
    error: Optional[str] = None
