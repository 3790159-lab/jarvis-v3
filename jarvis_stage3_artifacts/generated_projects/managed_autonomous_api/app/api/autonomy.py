from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.autonomy_control import AutonomyControlService


router = APIRouter(prefix="/api/autonomy", tags=["autonomy"])


class ActionEvaluationRequest(BaseModel):
    agent_id: str
    action_type: str
    requested_tools: List[str] = Field(default_factory=list)
    objective: str


class ApprovalDecisionRequest(BaseModel):
    decision: str


@router.get("/health")
def autonomy_health():
    return {
        "status": "healthy",
        "service": "managed_autonomy"
    }


@router.post("/score")
def score_agents():
    service = AutonomyControlService()
    return service.score_agents()


@router.get("/scores")
def get_scores():
    service = AutonomyControlService()
    return service.get_scores()


@router.post("/evaluate")
def evaluate_action(payload: ActionEvaluationRequest):
    service = AutonomyControlService()
    return service.evaluate_action(
        agent_id=payload.agent_id,
        action_type=payload.action_type,
        requested_tools=payload.requested_tools,
        objective=payload.objective
    )


@router.get("/approvals")
def list_approvals():
    service = AutonomyControlService()
    return service.list_approvals()


@router.post("/approvals/{approval_id}")
def decide_approval(approval_id: str, payload: ApprovalDecisionRequest):
    service = AutonomyControlService()
    return service.decide_approval(approval_id, payload.decision)


@router.post("/auto-disable")
def auto_disable():
    service = AutonomyControlService()
    return service.auto_disable_low_score_agents()
