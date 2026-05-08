from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.autonomous_decision_engine import service

router = APIRouter(prefix="/api/autonomous-decision", tags=["autonomous-decision"])


class DecideRequest(BaseModel):
    goal: str
    mission_type: str = ""
    risk_level: str = "medium"
    has_memory: bool = True
    requires_tools: bool = False
    requires_approval: bool = False
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    loop_enabled: bool = False
    loop_max_steps: int = 5


class ExecuteNowRequest(BaseModel):
    base_url: str = "http://127.0.0.1:8010"


@router.get("/health")
def decision_health():
    return service.health()


@router.post("/reconcile-legacy")
def reconcile_legacy():
    return service.reconcile_legacy_runs()


@router.get("/runs")
def list_runs():
    return service.list_runs()


@router.get("/runs/{decision_id}")
def get_run(decision_id: str):
    return service.get_run(decision_id)


@router.post("/decide")
def decide(req: DecideRequest):
    if not req.goal.strip():
        raise HTTPException(status_code=400, detail="goal is required")

    return service.decide(
        goal=req.goal,
        mission_type=req.mission_type,
        risk_level=req.risk_level,
        has_memory=req.has_memory,
        requires_tools=req.requires_tools,
        requires_approval=req.requires_approval,
        context=req.context or {},
        loop_enabled=req.loop_enabled,
        loop_max_steps=req.loop_max_steps,
    )


@router.post("/runs/{decision_id}/execute-now")
def execute_now(decision_id: str, req: ExecuteNowRequest):
    return service.execute_now(
        decision_id=decision_id,
        base_url=req.base_url,
    )
