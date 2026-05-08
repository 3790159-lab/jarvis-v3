from fastapi import APIRouter

from app.services.active_run_store import active_run_stats, list_active_runs
from app.services.continuity import recover_stale_active_runs, resume_incomplete_missions
from app.services.task_queue import list_queue, queue_stats
from app.services.worker_loop import get_worker_state, run_worker_iteration
from app.services.worker_registry import list_workers, registry_stats

router = APIRouter()

@router.get("/worker/status")
def worker_status():
    return {
        "worker": get_worker_state(),
        "registry": list_workers(),
        "registry_stats": registry_stats(),
        "active_runs": list_active_runs(),
        "active_run_stats": active_run_stats(),
        "queue": queue_stats(),
        "items": list_queue(limit=50),
    }

@router.post("/worker/run-once")
def worker_run_once():
    return run_worker_iteration()

@router.post("/worker/recover-stale")
def worker_recover_stale():
    return recover_stale_active_runs()

@router.post("/worker/resume-missions")
def worker_resume_missions():
    return resume_incomplete_missions()