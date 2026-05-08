from fastapi import APIRouter, HTTPException

from app.services.mission_memory import get_memory, memory_stats
from app.services.mission_store import get_mission, mission_stats, update_mission_status
from app.services.task_queue import cancel_mission_queue_items, mission_queue_items, queue_stats, retry_failed_tasks
from app.services.supervisor_core import list_all_missions, read_mission

router = APIRouter()

@router.get("/missions")
def missions():
    return {
        "items": list_all_missions(limit=100),
        "mission_stats": mission_stats(),
        "queue_stats": queue_stats(),
        "memory_stats": memory_stats(),
    }

@router.get("/missions/{mission_id}")
def mission_by_id(mission_id: str):
    item = read_mission(mission_id)
    if not item:
        raise HTTPException(status_code=404, detail="Mission not found")
    return item

@router.get("/missions/{mission_id}/memory")
def mission_memory(mission_id: str):
    return get_memory(mission_id)

@router.get("/missions/{mission_id}/queue")
def mission_queue(mission_id: str):
    return {"items": mission_queue_items(mission_id)}

@router.post("/missions/{mission_id}/retry")
def mission_retry(mission_id: str):
    item = get_mission(mission_id)
    if not item:
        raise HTTPException(status_code=404, detail="Mission not found")
    result = retry_failed_tasks(mission_id)
    update_mission_status(mission_id, "planned", "Retry requested via API.")
    return {"mission_id": mission_id, **result}

@router.post("/missions/{mission_id}/cancel")
def mission_cancel(mission_id: str):
    item = get_mission(mission_id)
    if not item:
        raise HTTPException(status_code=404, detail="Mission not found")
    result = cancel_mission_queue_items(mission_id)
    update_mission_status(mission_id, "cancelled", "Cancellation requested via API.")
    return {"mission_id": mission_id, **result}