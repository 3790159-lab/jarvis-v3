from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.hitl_approvals import service

router = APIRouter(prefix="/api/hitl", tags=["hitl"])


class CreateApprovalRequest(BaseModel):
    mission_id: str
    action_type: str
    proposed_action: str
    reason: str
    risk_level: str
    agent_id: str = ""
    agent_score: float = 0.0
    payload: Optional[Dict[str, Any]] = Field(default_factory=dict)


class DecideApprovalRequest(BaseModel):
    decision: str
    operator_note: str = ""


@router.get("/health")
def hitl_health():
    return service.health()


@router.get("/approvals")
def list_approvals(status: str | None = Query(default=None)):
    return service.list_approvals(status=status)


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str):
    return service.get_approval(approval_id)


@router.post("/approvals")
def create_approval(req: CreateApprovalRequest):
    if not req.mission_id.strip():
        raise HTTPException(status_code=400, detail="mission_id is required")
    if not req.action_type.strip():
        raise HTTPException(status_code=400, detail="action_type is required")
    if not req.proposed_action.strip():
        raise HTTPException(status_code=400, detail="proposed_action is required")
    if not req.reason.strip():
        raise HTTPException(status_code=400, detail="reason is required")

    return service.create_approval(
        mission_id=req.mission_id,
        action_type=req.action_type,
        proposed_action=req.proposed_action,
        reason=req.reason,
        risk_level=req.risk_level,
        agent_id=req.agent_id,
        agent_score=req.agent_score,
        payload=req.payload or {},
    )


@router.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, req: DecideApprovalRequest):
    try:
        return service.decide(
            approval_id=approval_id,
            decision=req.decision,
            operator_note=req.operator_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
