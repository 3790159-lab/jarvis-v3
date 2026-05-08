from __future__ import annotations

from app.runtime import (
    MissionStatus,
    TaskStatus,
    missions_store,
    tasks_store,
    transition_mission,
)


def get_mission_tasks(mission_id: str):
    return [t for t in tasks_store.list_all(refresh=True) if t.mission_id == mission_id]


def get_mission_task_summary(mission_id: str) -> dict:
    tasks = get_mission_tasks(mission_id)

    queued = [t for t in tasks if t.status == TaskStatus.QUEUED.value]
    running = [t for t in tasks if t.status == TaskStatus.RUNNING.value]
    retrying = [t for t in tasks if t.status == TaskStatus.RETRYING.value]
    completed = [t for t in tasks if t.status == TaskStatus.COMPLETED.value]
    failed = [t for t in tasks if t.status in [TaskStatus.FAILED.value, TaskStatus.FAILED_TIMEOUT.value]]

    return {
        "mission_id": mission_id,
        "task_count": len(tasks),
        "queued": len(queued),
        "running": len(running),
        "retrying": len(retrying),
        "completed": len(completed),
        "failed": len(failed),
        "all_completed": len(tasks) > 0 and len(completed) == len(tasks),
    }


def auto_complete_mission_if_done(mission_id: str) -> dict:
    mission = missions_store.get(mission_id, refresh=True)
    if not mission:
        return {"updated": False, "reason": "mission_not_found"}

    summary = get_mission_task_summary(mission_id)

    if summary["all_completed"] and mission.status != MissionStatus.COMPLETED.value:
        transition_mission(mission_id, MissionStatus.COMPLETED)
        return {"updated": True, "new_status": MissionStatus.COMPLETED.value, "summary": summary}

    if summary["failed"] > 0 and mission.status not in [MissionStatus.FAILED.value, MissionStatus.COMPLETED.value]:
        transition_mission(mission_id, MissionStatus.FAILED, error="One or more mission tasks failed")
        return {"updated": True, "new_status": MissionStatus.FAILED.value, "summary": summary}

    return {"updated": False, "summary": summary}
