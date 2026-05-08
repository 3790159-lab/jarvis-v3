from __future__ import annotations

from app.runtime import append_runtime_log, tasks_store, TaskStatus


def prune_old_completed_tasks(keep_last: int = 50) -> dict:
    removed = tasks_store.prune_completed(keep_last=keep_last)
    append_runtime_log(f"Maintenance pruned completed tasks removed={removed}")
    return {
        "removed": removed,
        "kept": keep_last,
    }


def get_queue_health() -> dict:
    tasks = tasks_store.list_all(refresh=True)

    return {
        "queued": len([t for t in tasks if t.status == TaskStatus.QUEUED.value]),
        "running": len([t for t in tasks if t.status == TaskStatus.RUNNING.value]),
        "retrying": len([t for t in tasks if t.status == TaskStatus.RETRYING.value]),
        "completed": len([t for t in tasks if t.status == TaskStatus.COMPLETED.value]),
        "failed": len([t for t in tasks if t.status in [TaskStatus.FAILED.value, TaskStatus.FAILED_TIMEOUT.value]]),
        "total": len(tasks),
    }
