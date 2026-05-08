from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    GENERAL = "general"
    REASONING = "reasoning"
    CODING = "coding"
    RESEARCH = "research"


class ProviderName(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    MOCK = "mock"


class DispatchRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    task_type: TaskType = TaskType.GENERAL
    preferred_provider: Optional[ProviderName] = None
    system_prompt: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DispatchResult(BaseModel):
    ok: bool
    task_type: TaskType
    selected_agent: str
    selected_provider: ProviderName
    attempted_providers: List[ProviderName] = Field(default_factory=list)
    response_text: str = ""
    raw_response: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class ProviderHealth(BaseModel):
    provider: ProviderName
    enabled: bool
    configured: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class RouterHealthResponse(BaseModel):
    status: str
    providers: List[ProviderHealth] = Field(default_factory=list)
    default_provider: ProviderName
