from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.ai_router import ProviderName, TaskType


class ExternalExecutorRequest(BaseModel):
    title: str = Field(..., min_length=1)
    objective: str = ""
    description: str = ""
    task_type: TaskType = TaskType.CODING
    preferred_provider: Optional[ProviderName] = None
    dry_run: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExternalExecutorResponse(BaseModel):
    ok: bool
    mode: str
    selected_provider: str
    selected_executor: str
    task_type: TaskType
    dry_run: bool
    execution_plan: Dict[str, Any] = Field(default_factory=dict)
    response_text: str = ""
    error: Optional[str] = None


class CloudProviderStatus(BaseModel):
    provider: str
    enabled: bool
    configured: bool
    recommended_for: List[str] = Field(default_factory=list)


class CloudExecutorHealthResponse(BaseModel):
    status: str
    providers: List[CloudProviderStatus] = Field(default_factory=list)
    external_executor_enabled: bool
    default_mode: str
