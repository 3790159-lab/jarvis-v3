from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.agent_feedback import service

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class EvaluateRequest(BaseModel):
    agent_id: str
    role: str = "unknown"
    stage_success: bool
    qa_pass: bool
    retry_count: int = 0


@router.get("/health")
def feedback_health():
    return service.health()


@router.get("/agents")
def list_feedback_agents():
    return service.list_agents()


@router.get("/agents/{agent_id}")
def get_feedback_agent(agent_id: str):
    return service.get_agent(agent_id)


@router.post("/evaluate")
def evaluate_agent(req: EvaluateRequest):
    return service.evaluate(
        agent_id=req.agent_id,
        role=req.role,
        stage_success=req.stage_success,
        qa_pass=req.qa_pass,
        retry_count=req.retry_count,
    )
