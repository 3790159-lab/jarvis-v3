from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.agent_adapters import invoke_adapter, list_adapters
from app.services.approval_store import (
    approval_is_approved,
    create_approval_request,
    list_approvals,
    set_approval_status,
)
from app.services.risk_policies import evaluate_risk
from app.services.semantic_memory import (
    ingest_packaged_mission_result,
    list_recent_memories,
    remember_memory,
    search_memories,
)
from app.services.task_marketplace import (
    complete_task,
    create_task,
    fail_task,
    lease_next_task,
    list_agents,
    list_tasks,
    register_agent,
)


router = APIRouter(tags=["agent_control_plane"])


class AgentRegisterRequest(BaseModel):
    agent_id: str
    display_name: str
    adapter_name: str
    capabilities: List[str] = Field(default_factory=list)
    enabled: bool = True
    weight: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskCreateRequest(BaseModel):
    title: str
    description: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    required_capabilities: List[str] = Field(default_factory=list)
    requested_tools: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LeaseTaskRequest(BaseModel):
    agent_id: str


class CompleteTaskRequest(BaseModel):
    result: Dict[str, Any] = Field(default_factory=dict)


class FailTaskRequest(BaseModel):
    error_text: str


class AdapterInvokeRequest(BaseModel):
    adapter_name: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    approval_id: Optional[str] = None
    dry_run: bool = False
    requested_capabilities: List[str] = Field(default_factory=list)
    requested_tools: List[str] = Field(default_factory=list)
    mission_id: Optional[str] = None
    step_id: Optional[str] = None


class ApprovalCreateRequest(BaseModel):
    summary: str
    risk_level: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    mission_id: Optional[str] = None
    step_id: Optional[str] = None


class ApprovalDecisionRequest(BaseModel):
    operator_note: str = ""


class MemoryRememberRequest(BaseModel):
    kind: str
    title: str
    text_body: str
    tags: List[str] = Field(default_factory=list)
    artifact_path: Optional[str] = None
    mission_id: Optional[str] = None
    source: Optional[str] = None


@router.get("/api/control/health")
def control_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "agent_control_plane",
        "features": [
            "agent_adapters",
            "task_marketplace",
            "semantic_memory",
            "human_approval",
            "risk_policies",
        ],
    }


@router.get("/api/agents/adapters")
def get_agent_adapters() -> Dict[str, Any]:
    return {
        "ok": True,
        "items": list_adapters(),
    }


@router.post("/api/agents/register")
def register_agent_endpoint(payload: AgentRegisterRequest) -> Dict[str, Any]:
    agent = register_agent(
        agent_id=payload.agent_id,
        display_name=payload.display_name,
        adapter_name=payload.adapter_name,
        capabilities=payload.capabilities,
        enabled=payload.enabled,
        weight=payload.weight,
        metadata=payload.metadata,
    )
    return {
        "ok": True,
        "agent": agent,
    }


@router.get("/api/agents/registry")
def list_agents_endpoint() -> Dict[str, Any]:
    items = list_agents()
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.post("/api/agents/tasks")
def create_task_endpoint(payload: TaskCreateRequest) -> Dict[str, Any]:
    policy = evaluate_risk(
        adapter_name=str(payload.metadata.get("adapter_name") or ""),
        payload=payload.payload,
        required_capabilities=payload.required_capabilities,
        requested_tools=payload.requested_tools,
    )

    approval_id = None
    if policy["requires_approval"]:
        approval = create_approval_request(
            summary=f"Task approval required: {payload.title}",
            risk_level=policy["risk_level"],
            payload={
                "title": payload.title,
                "description": payload.description,
                "payload": payload.payload,
                "requested_tools": payload.requested_tools,
            },
        )
        approval_id = approval.get("approval_id")

    task = create_task(
        title=payload.title,
        description=payload.description,
        payload=payload.payload,
        required_capabilities=payload.required_capabilities,
        risk_level=policy["risk_level"],
        approval_required=policy["requires_approval"],
        approval_id=approval_id,
        metadata={
            **payload.metadata,
            "policy_reason": policy["reason"],
            "requested_tools": payload.requested_tools,
        },
    )

    return {
        "ok": True,
        "task": task,
        "policy": policy,
        "approval_id": approval_id,
    }


@router.get("/api/agents/tasks")
def list_tasks_endpoint(status: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    items = list_tasks(status=status)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.post("/api/agents/tasks/lease")
def lease_task_endpoint(payload: LeaseTaskRequest) -> Dict[str, Any]:
    task = lease_next_task(payload.agent_id)
    return {
        "ok": True,
        "task": task,
    }


@router.post("/api/agents/tasks/{task_id}/complete")
def complete_task_endpoint(task_id: str, payload: CompleteTaskRequest) -> Dict[str, Any]:
    task = complete_task(task_id, payload.result)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "ok": True,
        "task": task,
    }


@router.post("/api/agents/tasks/{task_id}/fail")
def fail_task_endpoint(task_id: str, payload: FailTaskRequest) -> Dict[str, Any]:
    task = fail_task(task_id, payload.error_text)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "ok": True,
        "task": task,
    }


@router.post("/api/agents/invoke")
def invoke_agent_endpoint(payload: AdapterInvokeRequest) -> Dict[str, Any]:
    policy = evaluate_risk(
        adapter_name=payload.adapter_name,
        payload=payload.payload,
        required_capabilities=payload.requested_capabilities,
        requested_tools=payload.requested_tools,
    )

    if policy["is_forbidden"]:
        raise HTTPException(status_code=403, detail=policy["reason"])

    if policy["requires_approval"] and not approval_is_approved(payload.approval_id):
        approval = create_approval_request(
            summary=f"Adapter invoke requires approval: {payload.adapter_name}",
            risk_level=policy["risk_level"],
            payload={
                "adapter_name": payload.adapter_name,
                "payload": payload.payload,
                "requested_capabilities": payload.requested_capabilities,
                "requested_tools": payload.requested_tools,
            },
            mission_id=payload.mission_id,
            step_id=payload.step_id,
        )
        return {
            "ok": False,
            "status": "approval_required",
            "policy": policy,
            "approval": approval,
        }

    if payload.dry_run:
        return {
            "ok": True,
            "status": "dry_run",
            "policy": policy,
        }

    result = invoke_adapter(payload.adapter_name, payload.payload)
    return {
        "ok": bool(result.get("ok")),
        "status": "invoked",
        "policy": policy,
        "result": result,
    }


@router.get("/api/approvals")
def approvals_endpoint(status: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    items = list_approvals(status=status)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.post("/api/approvals/request")
def create_approval_endpoint(payload: ApprovalCreateRequest) -> Dict[str, Any]:
    item = create_approval_request(
        summary=payload.summary,
        risk_level=payload.risk_level,
        payload=payload.payload,
        mission_id=payload.mission_id,
        step_id=payload.step_id,
    )
    return {
        "ok": True,
        "approval": item,
    }


@router.post("/api/approvals/{approval_id}/approve")
def approve_endpoint(approval_id: str, payload: ApprovalDecisionRequest) -> Dict[str, Any]:
    item = set_approval_status(approval_id, "approved", payload.operator_note)
    if not item:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return {
        "ok": True,
        "approval": item,
    }


@router.post("/api/approvals/{approval_id}/reject")
def reject_endpoint(approval_id: str, payload: ApprovalDecisionRequest) -> Dict[str, Any]:
    item = set_approval_status(approval_id, "rejected", payload.operator_note)
    if not item:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return {
        "ok": True,
        "approval": item,
    }


@router.post("/api/memory/remember")
def remember_memory_endpoint(payload: MemoryRememberRequest) -> Dict[str, Any]:
    item = remember_memory(
        kind=payload.kind,
        title=payload.title,
        text_body=payload.text_body,
        tags=payload.tags,
        artifact_path=payload.artifact_path,
        mission_id=payload.mission_id,
        source=payload.source,
    )
    return {
        "ok": True,
        "memory": item,
    }


@router.get("/api/memory/search")
def search_memory_endpoint(q: str = Query(...), limit: int = Query(default=10)) -> Dict[str, Any]:
    items = search_memories(q, limit=limit)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.get("/api/memory/recent")
def recent_memory_endpoint(limit: int = Query(default=20)) -> Dict[str, Any]:
    items = list_recent_memories(limit=limit)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.post("/api/memory/ingest-mission/{mission_id}")
def ingest_mission_memory_endpoint(mission_id: str) -> Dict[str, Any]:
    try:
        result = ingest_packaged_mission_result(mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "ok": True,
        "result": result,
    }