"""Phase I.1: Deduplicate night tasks on schedule_all_phases."""
from __future__ import annotations

import sys
import os
import threading
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_scheduler_with_tasks(existing_tasks):
    """Create a minimal mock scheduler with given tasks."""
    sched = MagicMock()
    sched._lock = threading.Lock()
    sched._tasks = list(existing_tasks)

    def add_task(action, params, cron, chat_id):
        task_id = f"task_{action}"
        sched._tasks.append({"action": action, "task_id": task_id, "cron": cron})
        return task_id

    sched.add_task.side_effect = add_task
    return sched


def test_schedule_all_phases_replaces_duplicates():
    """Calling schedule_all_phases twice must not accumulate night_* tasks."""
    from app.services.night_workflows import NightWorkflow

    svc = NightWorkflow()

    # Pre-populate 5 night tasks (simulating first startup)
    existing = [
        {"action": f"night_{p}", "task_id": f"old_{p}", "cron": "0 22 * * *"}
        for p in ["winddown", "deep_work", "self_improve", "morning_prep", "wakeup"]
    ]
    sched = _make_scheduler_with_tasks(existing)

    # Second call — should replace, not append
    svc.schedule_all_phases(scheduler=sched)

    night_tasks = [t for t in sched._tasks if str(t.get("action", "")).startswith("night_")]
    assert len(night_tasks) == 5, (
        f"Expected exactly 5 night tasks after second call, got {len(night_tasks)}: {night_tasks}"
    )


def test_starting_twice_yields_5_tasks_not_10():
    """Simulates two startups — result must be 5 night tasks, not 10."""
    from app.services.night_workflows import NightWorkflow

    svc = NightWorkflow()
    sched = _make_scheduler_with_tasks([])

    # First startup
    svc.schedule_all_phases(scheduler=sched)
    after_first = len([t for t in sched._tasks if str(t.get("action", "")).startswith("night_")])
    assert after_first == 5, f"After first call expected 5, got {after_first}"

    # Second startup (simulates restart)
    svc.schedule_all_phases(scheduler=sched)
    after_second = len([t for t in sched._tasks if str(t.get("action", "")).startswith("night_")])
    assert after_second == 5, (
        f"After second call expected still 5, got {after_second} (duplicates created!)"
    )


def test_non_night_tasks_preserved_on_dedup():
    """Deduplication must not remove non-night tasks (reminders, etc.)."""
    from app.services.night_workflows import NightWorkflow

    svc = NightWorkflow()
    existing = [
        {"action": "remind", "task_id": "remind_1", "cron": "0 9 * * *"},
        {"action": "night_winddown", "task_id": "old_wind", "cron": "0 22 * * *"},
    ]
    sched = _make_scheduler_with_tasks(existing)

    svc.schedule_all_phases(scheduler=sched)

    remind_tasks = [t for t in sched._tasks if t.get("action") == "remind"]
    assert len(remind_tasks) == 1, "remind task must be preserved"
