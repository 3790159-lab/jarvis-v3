from typing import Any, Dict, List

from app.services.active_run_store import list_active_runs, remove_active_snapshot, stale_active_runs
from app.services.mission_memory import append_memory
from app.services.mission_store import get_mission, list_missions, mark_task_status, update_mission_status
from app.services.task_queue import enqueue_tasks, list_queue, requeue_task
from app.services.worker_registry import stale_worker_ids


def _queue_index() -> set[tuple[str, str]]:
    idx = set()
    for item in list_queue(limit=5000):
        idx.add((str(item.get("mission_id")), str(item.get("task_id"))))
    return idx


def resume_incomplete_missions() -> Dict[str, Any]:
    missions = list_missions(limit=500)
    queue_idx = _queue_index()

    resumed = 0
    skipped = 0
    tasks_enqueued = 0

    for mission in missions:
        status = str(mission.get("status", ""))
        mission_id = str(mission.get("mission_id", ""))
        if status in {"completed", "failed"}:
            skipped += 1
            continue

        completed_or_failed = {
            str(r.get("task_id"))
            for r in mission.get("task_results", [])
            if str(r.get("status")) in {"completed", "failed"}
        }

        to_enqueue: List[Dict[str, Any]] = []
        for task in mission.get("tasks", []):
            task_id = str(task.get("task_id"))
            if task_id in completed_or_failed:
                continue
            if (mission_id, task_id) in queue_idx:
                continue
            task = dict(task)
            task["status"] = "queued"
            to_enqueue.append(task)

        if to_enqueue:
            enqueue_tasks(mission_id, to_enqueue)
            update_mission_status(mission_id, "planned", "Mission resumed and missing tasks re-queued.")
            append_memory(mission_id, "mission_resumed", {"tasks_requeued": len(to_enqueue)})
            resumed += 1
            tasks_enqueued += len(to_enqueue)
        else:
            skipped += 1

    return {
        "missions_resumed": resumed,
        "missions_skipped": skipped,
        "tasks_requeued": tasks_enqueued,
    }


def recover_stale_active_runs(worker_timeout_seconds: int = 15, active_timeout_seconds: int = 20) -> Dict[str, Any]:
    stale_workers = set(stale_worker_ids(timeout_seconds=worker_timeout_seconds))
    stale_runs = stale_active_runs(timeout_seconds=active_timeout_seconds)

    recovered = 0
    ignored = 0

    for item in stale_runs:
        mission_id = str(item.get("mission_id", ""))
        task_id = str(item.get("task_id", ""))
        worker_id = str(item.get("worker_id", ""))

        if stale_workers and worker_id not in stale_workers:
            ignored += 1
            continue

        requeue_task(
            mission_id=mission_id,
            task_id=task_id,
            backoff_seconds=1,
            last_error="Recovered from stale active run snapshot",
        )
        mark_task_status(mission_id, task_id, "queued")
        update_mission_status(mission_id, "planned", "Mission recovered from stale worker state.")
        append_memory(mission_id, "stale_recovered", {"task_id": task_id, "worker_id": worker_id})
        remove_active_snapshot(mission_id, task_id)
        recovered += 1

    return {
        "stale_workers": list(stale_workers),
        "stale_runs_found": len(stale_runs),
        "recovered_tasks": recovered,
        "ignored_tasks": ignored,
    }