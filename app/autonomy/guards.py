from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .store import JsonStore, ensure_runtime_bucket, utc_now_iso


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


class AutonomyGuardManager:
    ACTIVE_PENDING_STATES = {"pending", "approved_ready"}

    def __init__(self, store: JsonStore, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.policy_path = self.store.root / "autonomy_policy.json"
        self._ensure_policy()

    def _ensure_policy(self) -> None:
        if not self.policy_path.exists():
            self.store.write_json(
                self.policy_path,
                {
                    "enabled": True,
                    "max_runs_per_hour": 8,
                    "max_replans_per_hour": 4,
                    "max_consecutive_failures": 3,
                    "max_pending_jobs_per_mission": 3,
                    "auto_pause_on_instability": True,
                    "allow_manual_continue_when_paused": False,
                    "instability_window_minutes": 60,
                    "memory_snapshot_on_success": True,
                    "memory_snapshot_on_guard_pause": True,
                },
            )

    def get_policy(self) -> Dict[str, Any]:
        self._ensure_policy()
        return self.store.read_json(self.policy_path, {})

    def update_policy(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        policy = self.get_policy()
        policy.update(patch or {})
        self.store.write_json(self.policy_path, policy)
        self.event_bus.publish(
            "autonomy_policy_updated",
            payload={"policy": policy},
            source="guard_manager",
        )
        return policy

    def _events_in_window(self, mission_id: str, minutes: int) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=minutes)
        result: List[Dict[str, Any]] = []
        for item in self.store.read_events(mission_id=mission_id, limit=500):
            created = _parse_iso(item.get("created_at"))
            if created and created >= threshold:
                result.append(item)
        return result

    def assess_mission(self, mission_id: str) -> Dict[str, Any]:
        policy = self.get_policy()
        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        jobs = self.store.list_jobs()

        instability_window_minutes = int(policy.get("instability_window_minutes", 60))
        events = self._events_in_window(mission_id, instability_window_minutes)

        runs_last_hour = sum(
            1 for e in events if e.get("type") in ("mission_run_completed", "mission_continue_completed")
        )
        replans_last_hour = sum(1 for e in events if e.get("type") == "mission_replanned")
        failures_last_hour = sum(
            1 for e in events if e.get("type") in ("mission_run_failed", "mission_continue_failed")
        )

        active_pending_jobs = [
            j for j in jobs
            if j.get("mission_id") == mission_id and j.get("status") in self.ACTIVE_PENDING_STATES
        ]
        approval_waiting_jobs = [
            j for j in jobs
            if j.get("mission_id") == mission_id and j.get("status") == "awaiting_approval"
        ]

        violations: List[Dict[str, Any]] = []

        if policy.get("enabled", True):
            if runs_last_hour >= int(policy.get("max_runs_per_hour", 8)):
                violations.append(
                    {"code": "max_runs_per_hour", "message": f"Runs in last hour: {runs_last_hour}"}
                )

            if replans_last_hour >= int(policy.get("max_replans_per_hour", 4)):
                violations.append(
                    {"code": "max_replans_per_hour", "message": f"Replans in last hour: {replans_last_hour}"}
                )

            if int(bucket.get("consecutive_failures", 0)) >= int(policy.get("max_consecutive_failures", 3)):
                violations.append(
                    {
                        "code": "max_consecutive_failures",
                        "message": f"Consecutive failures: {bucket.get('consecutive_failures', 0)}",
                    }
                )

            if len(active_pending_jobs) >= int(policy.get("max_pending_jobs_per_mission", 3)):
                violations.append(
                    {
                        "code": "max_pending_jobs_per_mission",
                        "message": f"Active pending jobs: {len(active_pending_jobs)}",
                    }
                )

        return {
            "mission_id": mission_id,
            "allowed": len(violations) == 0,
            "violations": violations,
            "metrics": {
                "runs_last_hour": runs_last_hour,
                "replans_last_hour": replans_last_hour,
                "failures_last_hour": failures_last_hour,
                "pending_jobs": len(active_pending_jobs),
                "awaiting_approval_jobs": len(approval_waiting_jobs),
                "consecutive_failures": int(bucket.get("consecutive_failures", 0)),
                "paused": bool(bucket.get("paused")),
                "status": bucket.get("status"),
            },
            "checked_at": utc_now_iso(),
        }

    def enforce(self, mission_id: str, scheduler: Any) -> Dict[str, Any]:
        policy = self.get_policy()
        assessment = self.assess_mission(mission_id)

        if assessment["allowed"]:
            return {"mission_id": mission_id, "action": "allow", "assessment": assessment}

        if policy.get("auto_pause_on_instability", True):
            reasons = "; ".join(v["message"] for v in assessment["violations"])
            pause_result = scheduler.pause_mission(
                mission_id=mission_id,
                reason=f"autonomy_guard:{reasons}",
            )
            self.event_bus.publish(
                "autonomy_auto_paused",
                mission_id=mission_id,
                payload={"assessment": assessment},
                severity="warning",
                source="guard_manager",
            )
            return {
                "mission_id": mission_id,
                "action": "auto_paused",
                "assessment": assessment,
                "pause_result": pause_result,
            }

        return {
            "mission_id": mission_id,
            "action": "deny_without_pause",
            "assessment": assessment,
        }
