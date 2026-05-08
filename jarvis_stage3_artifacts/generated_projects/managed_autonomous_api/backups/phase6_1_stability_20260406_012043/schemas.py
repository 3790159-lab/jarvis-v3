from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


AgentRole = Literal[
    "supervisor",
    "planner",
    "executor",
    "qa",
    "code",
    "research",
    "bootstrap",
    "memory",
    "policy"
]

AgentStatus = Literal["active", "disabled", "quarantined", "draft"]
AgentHealth = Literal["unknown", "healthy", "degraded", "unhealthy"]


class AgentPolicy(BaseModel):
    profile: str = "safe"
    allow_shell: bool = False
    allow_python: bool = False
    allow_file_read: bool = True
    allow_file_write: bool = False
    allow_http: bool = False
    allow_registry_write: bool = False
    max_steps: int = 8
    timeout_seconds: int = 60
    risk_level: str = "low"


class AgentRegistryEntry(BaseModel):
    agent_id: str
    name: str
    role: AgentRole
    description: str = ""
    model_profile: str = "default"
    policy: AgentPolicy
    allowed_tools: List[str] = Field(default_factory=list)
    status: AgentStatus = "active"
    health: AgentHealth = "unknown"
    version: str = "1.0.0"
    created_by: str = "phase5_setup"
    owner: str = "system"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentRegistryDocument(BaseModel):
    version: int = 1
    updated_at: Optional[datetime] = None
    agents: List[AgentRegistryEntry] = Field(default_factory=list)


class AgentTaskEnvelope(BaseModel):
    task_id: str
    mission_id: Optional[str] = None
    objective: str
    constraints: Dict[str, Any] = Field(default_factory=dict)
    requested_tools: List[str] = Field(default_factory=list)
    input_payload: Dict[str, Any] = Field(default_factory=dict)


class AgentResultEnvelope(BaseModel):
    agent_id: str
    task_id: str
    status: Literal["completed", "failed", "blocked", "needs_approval"]
    summary: str
    artifacts: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    next_action: Optional[str] = None
    confidence: float = 0.5
    requires_human: bool = False
    output_payload: Dict[str, Any] = Field(default_factory=dict)
