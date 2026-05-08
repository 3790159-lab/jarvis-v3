from fastapi import APIRouter

from app.services.context_manager import ensure_context_compressed, get_resume_bundle
from app.services.mission_memory import get_memory, memory_stats

router = APIRouter()

@router.get("/memory/stats")
def memory_stats_endpoint():
    return memory_stats()

@router.get("/memory/{mission_id}")
def memory_by_mission(mission_id: str):
    return get_memory(mission_id)

@router.get("/memory/{mission_id}/context")
def memory_context(mission_id: str):
    return get_resume_bundle(mission_id)

@router.post("/memory/{mission_id}/compress")
def memory_compress(mission_id: str):
    return ensure_context_compressed(mission_id)