from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .store import JsonStore, ensure_runtime_bucket, utc_now_iso


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


class MemoryManager:
    RELEVANT_EVENT_TYPES = {
        "mission_continue_completed",
        "mission_continue_failed",
        "mission_replanned",
        "mission_paused",
        "mission_resumed",
        "autonomy_auto_paused",
        "schedule_created",
        "schedule_due",
        "mission_admin_reset",
        "approval_requested",
        "approval_resolved",
    }

    def __init__(self, store: JsonStore, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.snapshots_file = self.store.root / "memory_snapshots.jsonl"

    def _append_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.snapshots_file, snapshot)

    def list_snapshots(self, mission_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.snapshots_file.exists():
            return []

        lines = self.snapshots_file.read_text(encoding="utf-8").splitlines()
        result: List[Dict[str, Any]] = []

        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if mission_id and item.get("mission_id") != mission_id:
                continue
            result.append(item)
            if len(result) >= limit:
                break

        result.reverse()
        return result

    def delete_snapshots(self, mission_id: str) -> int:
        if not self.snapshots_file.exists():
            return 0

        lines = self.snapshots_file.read_text(encoding="utf-8").splitlines()
        kept = []
        removed = 0

        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                kept.append(line)
                continue

            if item.get("mission_id") == mission_id:
                removed += 1
                continue

            kept.append(json.dumps(item, ensure_ascii=False))

        content = ""
        if kept:
            content = "\n".join(kept) + "\n"
        self.snapshots_file.write_text(content, encoding="utf-8")
        return removed

    def _relevant_events(self, mission_id: str, limit: int = 40) -> List[Dict[str, Any]]:
        events = self.store.read_events(mission_id=mission_id, limit=200)
        filtered = [e for e in events if e.get("type") in self.RELEVANT_EVENT_TYPES]
        return filtered[-limit:]

    def _compressed_event_summary(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        compact: List[Dict[str, Any]] = []
        for e in events[-12:]:
            compact.append(
                {
                    "type": e.get("type"),
                    "severity": e.get("severity"),
                    "created_at": e.get("created_at"),
                    "source": e.get("source"),
                    "payload_keys": sorted(list((e.get("payload") or {}).keys())),
                }
            )
        return compact

    def create_snapshot(self, mission_id: str, trigger: str = "manual") -> Dict[str, Any]:
        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        replans = self.store.read_replans(mission_id=mission_id, limit=10)
        relevant_events = self._relevant_events(mission_id, limit=40)

        snapshot = {
            "snapshot_id": f"snap_{uuid.uuid4().hex[:12]}",
            "mission_id": mission_id,
            "trigger": trigger,
            "created_at": utc_now_iso(),
            "runtime": {
                "status": bucket.get("status"),
                "paused": bucket.get("paused"),
                "pause_reason": bucket.get("pause_reason"),
                "run_count": bucket.get("run_count"),
                "failure_count": bucket.get("failure_count"),
                "consecutive_failures": bucket.get("consecutive_failures"),
                "replan_count": bucket.get("replan_count"),
                "continue_count": bucket.get("continue_count"),
                "last_run_at": bucket.get("last_run_at"),
                "last_success_at": bucket.get("last_success_at"),
                "last_failure_at": bucket.get("last_failure_at"),
            },
            "recent_replans": replans[-3:],
            "relevant_event_summary": self._compressed_event_summary(relevant_events),
            "resume_hint": {
                "recommended_entrypoint": "continue_latest_checkpoint",
                "preferred_mode": "safe_resume",
                "notes": [
                    "Use latest successful continuation state if available",
                    "Prefer selective continuation over full replay",
                    "Check guard policy before autonomous continuation",
                    "Check mode policy before autonomous continuation",
                ],
            },
        }

        self._append_snapshot(snapshot)
        self.event_bus.publish(
            "memory_snapshot_created",
            mission_id=mission_id,
            payload={"snapshot_id": snapshot["snapshot_id"], "trigger": trigger},
            source="memory_manager",
        )
        return snapshot

    def build_resume_bundle(self, mission_id: str) -> Dict[str, Any]:
        runtime = self.store.get_runtime()
        bucket = ensure_runtime_bucket(runtime, mission_id)
        snapshots = self.list_snapshots(mission_id=mission_id, limit=10)
        relevant_events = self._relevant_events(mission_id, limit=20)
        latest_snapshot = snapshots[-1] if snapshots else None

        return {
            "mission_id": mission_id,
            "built_at": utc_now_iso(),
            "current_runtime": bucket,
            "latest_snapshot": latest_snapshot,
            "recent_relevant_events": relevant_events[-8:],
            "resume_guidance": {
                "can_resume": not bool(bucket.get("paused")),
                "needs_operator_review": bucket.get("status") == "attention",
                "recommended_action": (
                    "resume_after_unpause" if bucket.get("paused")
                    else "continue_from_latest_checkpoint"
                ),
            },
        }
