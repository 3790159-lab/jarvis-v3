from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.ai_router import ProviderName, TaskType


class ProviderRoutingDecision(BaseModel):
    ok: bool
    task_type: TaskType
    preferred_provider: Optional[ProviderName] = None
    resolved_mode: str
    resolved_profile: str
    selected_provider: Optional[ProviderName] = None
    provider_chain: List[ProviderName] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    available_providers: List[ProviderName] = Field(default_factory=list)
    error: Optional[str] = None


class ProviderRoutingRequest(BaseModel):
    task_type: TaskType
    preferred_provider: Optional[ProviderName] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProviderRoutingHealthResponse(BaseModel):
    status: str
    execution_mode: str
    routing_profile: str
    available_providers: List[ProviderName] = Field(default_factory=list)
    provider_status: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
