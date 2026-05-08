from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.reliability import ReliabilityManager


router = APIRouter(prefix="/api/reliability", tags=["reliability"])


class ReliableRunRequest(BaseModel):
    task_id: str
    mission_id: Optional[str] = None
    objective: str
    task_type: Optional[str] = None
    requested_tools: List[str] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    input_payload: Dict[str, Any] = Field(default_factory=dict)


class StageUpdateRequest(BaseModel):
    stage_name: str
    status: str
    note: Optional[str] = None


class RetryRequest(BaseModel):
    reason: str


class QuarantineRequest(BaseModel):
    reason: str


@router.get("/health")
def reliability_health():
    manager = ReliabilityManager()
    return {
        "status": "healthy",
        "service": "reliability_layer",
        "runs_count": len(manager.list_runs())
    }


@router.post("/runs/start")
def start_run(payload: ReliableRunRequest):
    manager = ReliabilityManager()
    return manager.start_reliable_run(
        task_id=payload.task_id,
        mission_id=payload.mission_id,
        objective=payload.objective,
        task_type=payload.task_type,
        requested_tools=payload.requested_tools,
        constraints=payload.constraints,
        input_payload=payload.input_payload
    )


@router.get("/runs")
def list_runs():
    manager = ReliabilityManager()
    runs = manager.list_runs()
    return {
        "status": "ok",
        "count": len(runs),
        "runs": runs
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    manager = ReliabilityManager()
    run = manager.get_run(run_id)
    if not run:
        return {"status": "not_found", "run_id": run_id}
    return {"status": "ok", "run": run}


@router.post("/runs/{run_id}/stage")
def update_stage(run_id: str, payload: StageUpdateRequest):
    manager = ReliabilityManager()
    run = manager.mark_stage(
        run_id=run_id,
        stage_name=payload.stage_name,
        status=payload.status,
        note=payload.note
    )
    return {"status": "ok", "run": run}


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: str, payload: RetryRequest):
    manager = ReliabilityManager()
    run = manager.create_retry(run_id, payload.reason)
    return {"status": "ok", "run": run}


@router.post("/runs/{run_id}/quarantine")
def quarantine_run(run_id: str, payload: QuarantineRequest):
    manager = ReliabilityManager()
    run = manager.quarantine_run(run_id, payload.reason)
    return {"status": "ok", "run": run}
