from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.mission_resume_store import (
    acquire_lock,
    create_or_update_snapshot,
    detect_stale_snapshots,
    get_snapshot,
    has_lock,
    list_snapshots,
    mark_stale_snapshots,
    mark_status,
    release_lock,
    resumable_steps,
)


router = APIRouter(tags=["resume_recovery"])


class StepModel(BaseModel):
    step_id: str
    title: str
    description: str
    task_type: str = "reasoning"
    preferred_provider: str | None = None
    status: str = "pending"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    response_text: str | None = None
    error: str | None = None


class MissionSnapshotRequest(BaseModel):
    mission_id: str
    objective: str
    steps: List[StepModel] = Field(default_factory=list)


@router.get("/api/autonomy/resume/health")
def resume_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "resume_recovery",
        "features": [
            "snapshots",
            "resume",
            "recover",
            "stale_detection",
            "lock_safety",
        ],
    }


@router.get("/api/autonomy/resume/snapshots")
def get_all_snapshots() -> Dict[str, Any]:
    items = list_snapshots()
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.get("/api/autonomy/resume/stale")
def get_stale(max_age_seconds: int = 300) -> Dict[str, Any]:
    items = detect_stale_snapshots(max_age_seconds=max_age_seconds)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.post("/api/autonomy/resume/stale/mark")
def mark_stale(max_age_seconds: int = 300) -> Dict[str, Any]:
    items = mark_stale_snapshots(max_age_seconds=max_age_seconds)
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.post("/api/autonomy/missions/snapshot")
def create_snapshot(payload: MissionSnapshotRequest) -> Dict[str, Any]:
    snapshot = create_or_update_snapshot(
        mission_id=payload.mission_id,
        objective=payload.objective,
        steps=[step.model_dump() for step in payload.steps],
        status="planned",
        active_step_id=None,
        error=None,
    )
    return {
        "ok": True,
        "snapshot": snapshot,
    }


@router.post("/api/autonomy/missions/{mission_id}/resume")
def resume_mission(mission_id: str) -> Dict[str, Any]:
    snapshot = get_snapshot(mission_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    if has_lock(mission_id):
        raise HTTPException(status_code=409, detail="Mission is already locked/running")

    if not acquire_lock(mission_id):
        raise HTTPException(status_code=409, detail="Could not acquire mission lock")

    try:
        steps_to_resume = resumable_steps(snapshot)
        updated = create_or_update_snapshot(
            mission_id=mission_id,
            objective=str(snapshot.get("objective") or ""),
            steps=steps_to_resume,
            status="running",
            active_step_id=(steps_to_resume[0].get("step_id") if steps_to_resume else None),
            error=None,
        )
        return {
            "ok": True,
            "action": "resume",
            "snapshot": updated,
            "resumable_step_count": len(steps_to_resume),
        }
    finally:
        release_lock(mission_id)


@router.post("/api/autonomy/missions/{mission_id}/recover")
def recover_mission(mission_id: str) -> Dict[str, Any]:
    snapshot = get_snapshot(mission_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    status = str(snapshot.get("status") or "").strip().lower()
    if status not in {"stale", "failed", "paused", "running", "planned"}:
        raise HTTPException(status_code=400, detail=f"Mission status is not recoverable: {status}")

    updated = mark_status(mission_id, "planned", error=None)
    return {
        "ok": True,
        "action": "recover",
        "snapshot": updated,
    }