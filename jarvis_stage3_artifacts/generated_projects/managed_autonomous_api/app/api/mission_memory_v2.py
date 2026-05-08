from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.mission_memory_v2 import service

router = APIRouter(prefix="/api/mission-memory-v2", tags=["mission-memory-v2"])


class RecordSuccessRequest(BaseModel):
    mission_type: str
    mission_id: str
    plan_summary: str
    reusable_pattern: str
    notes: str = ""


class RecordFailureRequest(BaseModel):
    mission_type: str
    mission_id: str
    failure_signature: str
    failed_stage: str
    notes: str = ""


@router.get("/health")
def memory_health():
    return service.health()


@router.get("/types")
def list_mission_types():
    return service.list_mission_types()


@router.get("/types/{mission_type}")
def get_mission_type(mission_type: str):
    return service.get_mission_type(mission_type)


@router.get("/suggest/{mission_type}")
def suggest_for_type(mission_type: str):
    return service.suggest(mission_type)


@router.post("/record-success")
def record_success(req: RecordSuccessRequest):
    return service.record_success(
        mission_type=req.mission_type,
        mission_id=req.mission_id,
        plan_summary=req.plan_summary,
        reusable_pattern=req.reusable_pattern,
        notes=req.notes,
    )


@router.post("/record-failure")
def record_failure(req: RecordFailureRequest):
    return service.record_failure(
        mission_type=req.mission_type,
        mission_id=req.mission_id,
        failure_signature=req.failure_signature,
        failed_stage=req.failed_stage,
        notes=req.notes,
    )
