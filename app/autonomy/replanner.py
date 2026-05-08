from __future__ import annotations

import uuid
from typing import Any, Dict, List

from .store import JsonStore, ensure_runtime_bucket, utc_now_iso


class ReplanEngine:
    def __init__(self, store: JsonStore, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus

    def create_revision(self, mission_id: str, trigger: str = "manual") -> Dict[str, Any]:
        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        recent_events = self.store.read_events(mission_id=mission_id, limit=25)

        repeated_failures = int(bucket.get("consecutive_failures", 0))
        last_events = [e.get("type") for e in recent_events[-8:]]

        proposed_actions: List[str] = []

        if repeated_failures >= 2:
            proposed_actions.extend([
                "Reduce parallelism for next continuation window",
                "Retry only tail tasks instead of full mission replay",
                "Insert diagnostic checkpoint before execution",
                "Mark failed branch for operator review if next run fails again",
            ])
        else:
            proposed_actions.extend([
                "Re-evaluate remaining tail tasks",
                "Preserve successful completed work",
                "Continue from the latest valid checkpoint",
            ])

        if "dependency_unblocked" in last_events:
            proposed_actions.append("Prioritize previously blocked tasks first")

        revision = {
            "revision_id": f"rev_{uuid.uuid4().hex[:12]}",
            "mission_id": mission_id,
            "trigger": trigger,
            "created_at": utc_now_iso(),
            "observed_state": {
                "status": bucket.get("status"),
                "paused": bucket.get("paused"),
                "run_count": bucket.get("run_count"),
                "failure_count": bucket.get("failure_count"),
                "consecutive_failures": bucket.get("consecutive_failures"),
                "last_event_types": last_events,
            },
            "strategy": "selective_tail_replan",
            "proposed_actions": proposed_actions,
            "next_step": "schedule_continuation_after_revision",
        }

        self.store.append_replan(revision)

        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        bucket["replan_count"] = int(bucket.get("replan_count", 0)) + 1
        bucket["last_replan_at"] = revision["created_at"]
        bucket["updated_at"] = revision["created_at"]
        self.store.save_runtime(runtime)

        self.event_bus.publish(
            "mission_replanned",
            mission_id=mission_id,
            payload={"revision_id": revision["revision_id"], "trigger": trigger},
            source="replan_engine",
        )
        return revision
