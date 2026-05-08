from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone

from .store import utc_now_iso


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


class MaintenanceManager:
    def __init__(self, store: Any, event_bus: Any, mode_manager: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.mode_manager = mode_manager

    def normalize_jobs_and_approvals(self) -> Dict[str, Any]:
        jobs = self.store.list_jobs()
        approvals = self.mode_manager.list_approvals()

        changed_jobs = 0
        changed_approvals = 0

        pending_approvals_by_reason: Dict[str, Dict[str, Any]] = {}
        for approval in approvals:
            if approval.get("status") != "pending":
                continue
            key = f"{approval.get('mission_id')}|{approval.get('reason')}|{approval.get('origin')}"
            pending_approvals_by_reason[key] = approval

        for job in jobs:
            if job.get("status") != "pending":
                continue

            key = f"{job.get('mission_id')}|{job.get('reason')}|scheduled"
            approval = pending_approvals_by_reason.get(key)
            if approval:
                approval.setdefault("payload", {})
                if not approval["payload"].get("job_id"):
                    approval["payload"]["job_id"] = job.get("job_id")
                    changed_approvals += 1

                job["status"] = "awaiting_approval"
                job["approval_id"] = approval.get("approval_id")
                job["last_error"] = "Normalized from legacy pending job to awaiting_approval"
                changed_jobs += 1

        self.store.save_jobs(jobs)
        self.store.write_json(self.mode_manager.approvals_file, approvals)

        self.event_bus.publish(
            "maintenance_normalized_jobs_and_approvals",
            payload={"changed_jobs": changed_jobs, "changed_approvals": changed_approvals},
            source="maintenance_manager",
        )
        return {
            "changed_jobs": changed_jobs,
            "changed_approvals": changed_approvals,
            "checked_at": utc_now_iso(),
        }

    def cleanup_orphan_approvals(self) -> Dict[str, Any]:
        jobs = self.store.list_jobs()
        approvals = self.mode_manager.list_approvals()

        job_ids = {j.get("job_id") for j in jobs if j.get("job_id")}
        kept = []
        removed = 0

        for approval in approvals:
            payload = approval.get("payload") or {}
            job_id = payload.get("job_id")

            if approval.get("status") == "pending" and job_id and job_id not in job_ids:
                removed += 1
                continue

            kept.append(approval)

        self.store.write_json(self.mode_manager.approvals_file, kept)

        self.event_bus.publish(
            "maintenance_cleanup_orphan_approvals",
            payload={"removed_approvals": removed},
            source="maintenance_manager",
        )
        return {"removed_approvals": removed, "checked_at": utc_now_iso()}

    def cleanup_legacy_pending_jobs(self) -> Dict[str, Any]:
        jobs = self.store.list_jobs()
        approvals = self.mode_manager.list_approvals()

        valid_approval_ids = {a.get("approval_id") for a in approvals if a.get("approval_id")}
        changed = 0

        for job in jobs:
            if job.get("status") != "pending":
                continue

            # legacy stale pending continuation without approval binding
            is_old_continuation = job.get("kind") == "mission_continuation"
            missing_approval = not job.get("approval_id")
            old_created = _parse_iso(job.get("created_at"))

            if is_old_continuation and missing_approval and old_created:
                job["status"] = "cancelled"
                job["finished_at"] = utc_now_iso()
                job["last_error"] = "Cancelled by maintenance: legacy orphan pending job"
                changed += 1
                continue

            if job.get("approval_id") and job.get("approval_id") not in valid_approval_ids:
                job["status"] = "cancelled"
                job["finished_at"] = utc_now_iso()
                job["last_error"] = "Cancelled by maintenance: missing linked approval"
                changed += 1

        self.store.save_jobs(jobs)

        self.event_bus.publish(
            "maintenance_cleanup_legacy_pending_jobs",
            payload={"changed_jobs": changed},
            source="maintenance_manager",
        )
        return {"changed_jobs": changed, "checked_at": utc_now_iso()}

    def cleanup_stale_runtime(self) -> Dict[str, Any]:
        runtime = self.store.get_runtime()
        jobs = self.store.list_jobs()
        pauses = self.store.get_pauses()

        referenced_missions = set()
        for job in jobs:
            if job.get("mission_id"):
                referenced_missions.add(job["mission_id"])
        for mission_id in pauses.keys():
            referenced_missions.add(mission_id)

        removed_runtime = 0
        new_runtime = {}

        for mission_id, state in runtime.items():
            if mission_id in referenced_missions or mission_id == "mission_custom_001":
                new_runtime[mission_id] = state
            else:
                removed_runtime += 1

        self.store.save_runtime(new_runtime)

        self.event_bus.publish(
            "maintenance_cleanup_stale_runtime",
            payload={"removed_runtime": removed_runtime},
            source="maintenance_manager",
        )
        return {"removed_runtime": removed_runtime, "checked_at": utc_now_iso()}

    def try_auto_unpause(self, guard_manager: Any, scheduler: Any, mission_id: str) -> Dict[str, Any]:
        pauses = self.store.get_pauses()
        runtime = self.store.get_runtime()
        pause_state = pauses.get(mission_id)
        runtime_state = runtime.get(mission_id, {})

        if not pause_state and not runtime_state.get("paused"):
            return {
                "mission_id": mission_id,
                "action": "noop",
                "reason": "mission_not_paused",
                "checked_at": utc_now_iso(),
            }

        assessment = guard_manager.assess_mission(mission_id)
        if not assessment.get("allowed"):
            return {
                "mission_id": mission_id,
                "action": "still_blocked",
                "assessment": assessment,
                "checked_at": utc_now_iso(),
            }

        result = scheduler.resume_mission(
            mission_id=mission_id,
            reason="maintenance_auto_unpause",
            auto_continue=False,
        )
        self.event_bus.publish(
            "maintenance_auto_unpause",
            mission_id=mission_id,
            payload={"assessment": assessment},
            source="maintenance_manager",
        )
        return {
            "mission_id": mission_id,
            "action": "resumed",
            "result": result,
            "checked_at": utc_now_iso(),
        }
