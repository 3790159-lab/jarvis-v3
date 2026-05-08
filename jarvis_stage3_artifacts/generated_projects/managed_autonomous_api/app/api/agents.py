from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from app.agents.policies import build_policy
from app.agents.registry import AgentRegistry
from app.agents.runtime import preflight_agent_task
from app.agents.schemas import AgentRegistryEntry, AgentTaskEnvelope


router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentCreateRequest(BaseModel):
    agent_id: str
    name: str
    role: str
    description: str = ""
    model_profile: str = "default"
    policy_profile: str = "safe"
    allowed_tools: List[str] = Field(default_factory=list)
    owner: str = "system"
    created_by: str = "api"


class AgentPreflightRequest(BaseModel):
    agent_id: str
    task_id: str
    mission_id: Optional[str] = None
    objective: str
    requested_tools: List[str] = Field(default_factory=list)
    constraints: dict = Field(default_factory=dict)
    input_payload: dict = Field(default_factory=dict)


@router.get("/health")
def agents_health():
    try:
        registry = AgentRegistry()
        agents = registry.list_agents()
        return {
            "status": "healthy",
            "service": "agent_control_plane",
            "agents_count": len(agents)
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "service": "agent_control_plane",
            "agents_count": 0,
            "error": str(exc)
        }


@router.post("/seed")
def seed_agents():
    try:
        registry = AgentRegistry()
        agents = registry.seed_defaults()
        return {
            "status": "ok",
            "seeded": len(agents),
            "agents": [a.model_dump(mode="json") for a in agents]
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "trace": traceback.format_exc()
        }


@router.get("/validate")
def validate_agents():
    registry = AgentRegistry()
    return registry.validate_registry()


@router.post("/refresh-health")
def refresh_agents_health():
    registry = AgentRegistry()
    return registry.refresh_health()


@router.post("/{agent_id}/disable")
def disable_agent(agent_id: str):
    registry = AgentRegistry()
    agent = registry.set_status(agent_id, "disabled")
    return {"status": "ok", "agent": agent.model_dump(mode="json")}


@router.post("/{agent_id}/enable")
def enable_agent(agent_id: str):
    registry = AgentRegistry()
    agent = registry.set_status(agent_id, "active")
    return {"status": "ok", "agent": agent.model_dump(mode="json")}


@router.get("")
def list_agents():
    registry = AgentRegistry()
    agents = registry.list_agents()
    return {
        "status": "ok",
        "count": len(agents),
        "agents": [a.model_dump(mode="json") for a in agents]
    }


@router.get("/{agent_id}")
def get_agent(agent_id: str):
    registry = AgentRegistry()
    agent = registry.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "status": "ok",
        "agent": agent.model_dump(mode="json")
    }


@router.post("")
def create_or_update_agent(payload: AgentCreateRequest):
    registry = AgentRegistry()
    entry = AgentRegistryEntry(
        agent_id=payload.agent_id,
        name=payload.name,
        role=payload.role,
        description=payload.description,
        model_profile=payload.model_profile,
        policy=build_policy(payload.policy_profile),
        allowed_tools=payload.allowed_tools,
        owner=payload.owner,
        created_by=payload.created_by
    )
    registry.upsert(entry)
    return {
        "status": "ok",
        "agent": entry.model_dump(mode="json")
    }


@router.post("/preflight")
def agent_preflight(payload: AgentPreflightRequest):
    task = AgentTaskEnvelope(
        task_id=payload.task_id,
        mission_id=payload.mission_id,
        objective=payload.objective,
        requested_tools=payload.requested_tools,
        constraints=payload.constraints,
        input_payload=payload.input_payload
    )
    result = preflight_agent_task(payload.agent_id, task)
    return {
        "status": "ok",
        "result": result.model_dump(mode="json")
    }
