from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    READY = "ready"
    QUEUED = "queued"
    RESERVED = "reserved"
    RUNNING = "running"
    WAITING_CONSULTATION = "waiting_consultation"
    WAITING_SUPPORT = "waiting_support"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    RETRY_SCHEDULED = "retry_scheduled"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


class AgentHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"


class ConsultationKind(str, Enum):
    CONSULT = "consult"
    SECOND_OPINION = "second_opinion"
    SUPPORT = "support"
    VALIDATION = "validation"
    HANDOFF = "handoff"


class SchemaExtensionProposal(BaseModel):
    field_name: str
    field_type: str = "str"
    reason: str
    scope: str = "mission"
    approved: bool = False


class ArtifactRef(BaseModel):
    name: str
    path: str
    kind: str = "file"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConsultRequest(BaseModel):
    request_id: str
    task_id: str
    from_agent_id: str
    kind: ConsultationKind = ConsultationKind.CONSULT
    requested_capability: str
    reason: str
    expected_output: str = ""
    priority: str = "normal"
    max_depth: int = 2


class ConsultResponse(BaseModel):
    request_id: str
    helper_agent_id: str
    summary: str
    recommended_actions: List[str] = Field(default_factory=list)
    confidence: float = 0.7


class AgentMetrics(BaseModel):
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate: float = 0.0


class AgentResult(BaseModel):
    agent_id: str
    status: str
    summary: str
    normalized_text: str = ""
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list)
    consult_requests: List[ConsultRequest] = Field(default_factory=list)
    support_needed: List[str] = Field(default_factory=list)
    confidence: float = 0.7
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)
    error: Optional[str] = None

    response_schema_version: str = "1.0"
    proposed_schema_extensions: List[SchemaExtensionProposal] = Field(default_factory=list)
    approved_schema_extensions: List[str] = Field(default_factory=list)
    dynamic_fields: Dict[str, Any] = Field(default_factory=dict)

    risk_notes: List[str] = Field(default_factory=list)
    handoff_notes: List[str] = Field(default_factory=list)
    validation_hints: List[str] = Field(default_factory=list)


class AgentCapabilityProfile(BaseModel):
    agent_id: str
    role: str
    capabilities: List[str] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)

    preferred_provider: str = "cloud"
    fallback_provider: str = "ollama"

    timeout_seconds: int = 180
    max_retries: int = 2

    health_state: AgentHealthState = AgentHealthState.HEALTHY
    cost_weight: float = 0.5
    quality_weight: float = 0.8

    requires_approval: bool = False
    consultation_roles_allowed: List[str] = Field(default_factory=list)

    dynamic_capability_proposals: List[str] = Field(default_factory=list)
    approved_dynamic_capabilities: List[str] = Field(default_factory=list)

    tool_bindings: Dict[str, Any] = Field(default_factory=dict)
    integration_bindings: Dict[str, Any] = Field(default_factory=dict)
    provider_bindings: Dict[str, Any] = Field(default_factory=dict)

    version: str = "1.0"

    def all_capabilities(self) -> List[str]:
        return sorted(set(self.capabilities + self.approved_dynamic_capabilities))


class TaskSpec(BaseModel):
    task_id: str
    title: str
    task_type: str = "generic"
    required_capability: str = "generic"
    depends_on: List[str] = Field(default_factory=list)

    priority: str = "normal"
    risk_level: str = "normal"
    max_retries: int = 2

    preferred_agent_id: Optional[str] = None
    consultation_allowed: bool = True
    max_consults: int = 2

    locality_requirement: bool = False
    storage_affinity: bool = False
    approval_required: bool = False

    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskRuntimeState(BaseModel):
    task: TaskSpec
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent_id: Optional[str] = None
    provider: str = ""
    attempts: int = 0
    consult_depth: int = 0
    result: Optional[AgentResult] = None
    qa: Optional[Dict[str, Any]] = None
    recovery: Optional[Dict[str, Any]] = None


class MissionSnapshot(BaseModel):
    mission_id: str
    goal: str
    status: str = "draft"
    tasks: List[TaskRuntimeState] = Field(default_factory=list)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    context_bundle: Dict[str, Any] = Field(default_factory=dict)
    audit_log: List[Dict[str, Any]] = Field(default_factory=list)