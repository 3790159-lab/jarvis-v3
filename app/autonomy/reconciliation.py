from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .store import utc_now_iso


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


class ReconciliationManager:
    def __init__(self, store: Any, event_bus: Any, mission_plan_store: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.mission_plan_store = mission_plan_store
        self.archived_jobs_file = self.store.root / "archived_jobs.jsonl"
        self.archived_missions_file = self.store.root / "archived_missions.jsonl"

    def reconcile_runtime_with_plans(self) -> Dict[str, Any]:
        runtime = self.store.get_runtime()
        planned = self.mission_plan_store.list_missions()
        planned_ids = {m.get("mission_id") for m in planned if m.get("mission_id")}

        removed = []
        kept = {}

        for mission_id, state in runtime.items():
            if mission_id in planned_ids or mission_id == "mission_custom_001":
                kept[mission_id] = state
            else:
                removed.append(mission_id)

        self.store.save_runtime(kept)

        self.event_bus.publish(
            "runtime_reconciled_with_plans",
            payload={"removed_runtime_missions": removed, "remaining": list(kept.keys())},
            source="reconciliation_manager",
        )

        return {
            "removed_runtime_missions": removed,
            "remaining_runtime_missions": list(kept.keys()),
            "checked_at": utc_now_iso(),
        }

    def archive_old_jobs(self, older_than_hours: int = 12) -> Dict[str, Any]:
        jobs = self.store.list_jobs()
        now = datetime.now(timezone.utc)

        active = []
        archived = 0

        for job in jobs:
            status = job.get("status")
            finished_at = _parse_iso(job.get("finished_at"))
            if status in ("completed", "failed", "cancelled") and finished_at:
                if now - finished_at > timedelta(hours=older_than_hours):
                    self.store.append_jsonl(self.archived_jobs_file, job)
                    archived += 1
                    continue
            active.append(job)

        self.store.save_jobs(active)

        self.event_bus.publish(
            "jobs_archived",
            payload={"archived_jobs": archived, "older_than_hours": older_than_hours},
            source="reconciliation_manager",
        )

        return {
            "archived_jobs": archived,
            "active_jobs": len(active),
            "checked_at": utc_now_iso(),
        }

    def archive_completed_missions(self, older_than_hours: int = 12) -> Dict[str, Any]:
        missions = self.mission_plan_store.list_missions()
        now = datetime.now(timezone.utc)

        kept = []
        archived = 0

        for mission in missions:
            status = mission.get("status")
            finished_at = _parse_iso(mission.get("finished_at"))
            if status == "completed" and finished_at:
                if now - finished_at > timedelta(hours=older_than_hours):
                    self.store.append_jsonl(self.archived_missions_file, mission)
                    archived += 1
                    continue
            kept.append(mission)

        self.mission_plan_store._write_all({"missions": kept})

        self.event_bus.publish(
            "missions_archived",
            payload={"archived_missions": archived, "older_than_hours": older_than_hours},
            source="reconciliation_manager",
        )

        return {
            "archived_missions": archived,
            "active_missions": len(kept),
            "checked_at": utc_now_iso(),
        }
