from __future__ import annotations

from fastapi import APIRouter, HTTPException
from app.services.execution_orchestrator import orchestrator

router = APIRouter(prefix="/api/execution", tags=["execution"])


@router.get("/health")
def execution_health():
    return orchestrator.health()


@router.post("/missions/{mission_id}/run")
def run_mission(mission_id: str):
    journal = orchestrator.start_run(mission_id)
    return {
        "ok": True,
        "action": "run",
        "mission_id": mission_id,
        "status": journal.get("status"),
        "current_stage": journal.get("current_stage"),
        "journal_path_hint": f"artifacts/execution_runtime/{mission_id}.json",
    }


@router.post("/missions/{mission_id}/stop")
def stop_mission(mission_id: str):
    try:
        journal = orchestrator.request_action(mission_id, "stop")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "action": "stop",
        "mission_id": mission_id,
        "requested_action": journal.get("requested_action"),
        "status": journal.get("status"),
    }


@router.post("/missions/{mission_id}/resume")
def resume_mission(mission_id: str):
    journal = orchestrator.resume_run(mission_id)
    return {
        "ok": True,
        "action": "resume",
        "mission_id": mission_id,
        "requested_action": journal.get("requested_action"),
        "status": journal.get("status"),
    }


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(mission_id: str):
    try:
        journal = orchestrator.request_action(mission_id, "cancel")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "action": "cancel",
        "mission_id": mission_id,
        "requested_action": journal.get("requested_action"),
        "status": journal.get("status"),
    }


@router.get("/missions/{mission_id}/journal")
def get_mission_journal(mission_id: str):
    return orchestrator.get_journal(mission_id)
