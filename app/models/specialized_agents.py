from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.ai_router import ProviderName, TaskType


class SpecializedExecutionRequest(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = ""
    objective: str = ""
    task_type: TaskType
    preferred_provider: Optional[ProviderName] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SpecializedExecutionResult(BaseModel):
    ok: bool
    task_type: TaskType
    selected_agent: str
    selected_provider: ProviderName
    attempted_providers: List[ProviderName] = Field(default_factory=list)
    response_text: str = ""
    prompt_used: str = ""
    error: Optional[str] = None
