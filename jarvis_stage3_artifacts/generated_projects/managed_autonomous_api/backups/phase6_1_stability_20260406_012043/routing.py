from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agents.registry import AgentRegistry
from app.agents.runtime import preflight_agent_task
from app.agents.schemas import AgentTaskEnvelope
from app.services.role_router import build_handoff_path, choose_agent


router = APIRouter(prefix="/api/routing", tags=["routing"])


class RoutingRequest(BaseModel):
    task_id: str
    mission_id: Optional[str] = None
    objective: str
    task_type: Optional[str] = None
    requested_tools: List[str] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    input_payload: Dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
def routing_health():
    registry = AgentRegistry()
    return {
        "status": "healthy",
        "service": "role_routing",
        "agents_count": len(registry.list_agents())
    }


@router.post("/decision")
def routing_decision(payload: RoutingRequest):
    decision = choose_agent(
        objective=payload.objective,
        requested_tools=payload.requested_tools,
        task_type=payload.task_type,
        input_payload=payload.input_payload
    )

    return {
        "status": "ok",
        "decision": decision,
        "handoff_path": build_handoff_path(decision["agent_id"])
    }


@router.post("/preflight")
def routing_preflight(payload: RoutingRequest):
    decision = choose_agent(
        objective=payload.objective,
        requested_tools=payload.requested_tools,
        task_type=payload.task_type,
        input_payload=payload.input_payload
    )

    task = AgentTaskEnvelope(
        task_id=payload.task_id,
        mission_id=payload.mission_id,
        objective=payload.objective,
        constraints=payload.constraints,
        requested_tools=payload.requested_tools,
        input_payload=payload.input_payload
    )

    result = preflight_agent_task(decision["agent_id"], task)

    return {
        "status": "ok",
        "decision": decision,
        "handoff_path": build_handoff_path(decision["agent_id"]),
        "preflight_result": result.model_dump(mode="json")
    }


@router.post("/handoff")
def routing_handoff(payload: RoutingRequest):
    decision = choose_agent(
        objective=payload.objective,
        requested_tools=payload.requested_tools,
        task_type=payload.task_type,
        input_payload=payload.input_payload
    )

    path = build_handoff_path(decision["agent_id"])
    stages = []

    for idx, agent_id in enumerate(path, start=1):
        stages.append({
            "order": idx,
            "agent_id": agent_id,
            "stage_name": f"stage_{idx}",
            "status": "planned"
        })

    return {
        "status": "ok",
        "decision": decision,
        "handoff_path": path,
        "stages": stages
    }
