from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.memory_store import MemoryStore
from app.services.reliability import ReliabilityManager


router = APIRouter(prefix="/api/memory", tags=["memory"])


class NoteRequest(BaseModel):
    note_type: str
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
def memory_health():
    store = MemoryStore()
    return {
        "status": "healthy",
        "service": "memory_store",
        "stats": store.stats()
    }


@router.post("/sync/runs")
def sync_runs_to_memory():
    store = MemoryStore()
    reliability = ReliabilityManager()
    runs = reliability.list_runs()

    synced = []
    for run in runs:
        synced.append(store.upsert_run_summary(run))

    return {
        "status": "ok",
        "synced_count": len(synced)
    }


@router.get("/runs/{run_id}")
def get_run_summary(run_id: str):
    store = MemoryStore()
    item = store.get_run_summary(run_id)
    if not item:
        return {"status": "not_found", "run_id": run_id}
    return {"status": "ok", "run_summary": item}


@router.get("/missions/{mission_id}")
def get_mission_summary(mission_id: str):
    store = MemoryStore()
    item = store.get_mission_summary(mission_id)
    if not item:
        return {"status": "not_found", "mission_id": mission_id}
    return {"status": "ok", "mission_summary": item}


@router.post("/notes")
def add_note(payload: NoteRequest):
    store = MemoryStore()
    note = store.add_note(payload.note_type, payload.text, payload.metadata)
    return {"status": "ok", "note": note}


@router.get("/notes")
def list_notes():
    store = MemoryStore()
    return {
        "status": "ok",
        "notes": store.list_notes()
    }
