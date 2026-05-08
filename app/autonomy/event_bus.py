from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .store import JsonStore, ensure_runtime_bucket, utc_now_iso


class EventBus:
    def __init__(self, store: JsonStore) -> None:
        self.store = store

    def publish(
        self,
        event_type: str,
        mission_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        severity: str = "info",
        source: str = "autonomy",
    ) -> Dict[str, Any]:
        event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": event_type,
            "mission_id": mission_id,
            "payload": payload or {},
            "severity": severity,
            "source": source,
            "created_at": utc_now_iso(),
        }
        self.store.append_event(event)

        if mission_id:
            runtime = self.store.get_runtime()
            bucket = ensure_runtime_bucket(runtime, mission_id)
            bucket["last_event_at"] = event["created_at"]
            bucket["updated_at"] = event["created_at"]

            if event_type in ("mission_run_started", "mission_continue_started"):
                bucket["status"] = "running"
            elif event_type in ("mission_run_completed", "mission_continue_completed"):
                bucket["status"] = "idle"
                bucket["last_success_at"] = event["created_at"]
                bucket["consecutive_failures"] = 0
            elif event_type in ("mission_run_failed", "mission_continue_failed"):
                bucket["status"] = "attention"
                bucket["last_failure_at"] = event["created_at"]
                bucket["failure_count"] = int(bucket.get("failure_count", 0)) + 1
                bucket["consecutive_failures"] = int(bucket.get("consecutive_failures", 0)) + 1

            self.store.save_runtime(runtime)

        return event

    def list_events(self, mission_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        return self.store.read_events(mission_id=mission_id, limit=limit)
