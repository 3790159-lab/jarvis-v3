from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Dict, List, Optional

from .store import utc_now_iso


def _safe_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_jsonable(v) for v in value]
    return f"<nonserializable:{type(value).__name__}>"


class MissionPlanStore:
    def __init__(self, store: Any, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.plans_file = self.store.root / "mission_plans.json"
        self.step_history_file = self.store.root / "mission_step_history.jsonl"
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not self.plans_file.exists():
            self.store.write_json(self.plans_file, {"missions": []})

    def _read_all(self) -> Dict[str, Any]:
        self._ensure_files()
        return self.store.read_json(self.plans_file, {"missions": []})

    def _write_all(self, data: Dict[str, Any]) -> None:
        self.store.write_json(self.plans_file, _safe_jsonable(data))

    def list_missions(self) -> List[Dict[str, Any]]:
        return self._read_all().get("missions", [])

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        for mission in self.list_missions():
            if mission.get("mission_id") == mission_id:
                return mission
        return None

    def create_mission(
        self,
        objective: str,
        template_id: str,
        steps: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        mission_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        data = self._read_all()
        missions = data.get("missions", [])

        mission_id = mission_id or f"msn_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        normalized_steps = []
        for index, step in enumerate(steps):
            step_copy = copy.deepcopy(step)
            step_copy.setdefault("step_id", f"step_{index+1:03d}")
            step_copy.setdefault("status", "pending")
            step_copy.setdefault("attempt_count", 0)
            step_copy.setdefault("repair_count", 0)
            step_copy.setdefault("last_error", None)
            step_copy.setdefault("started_at", None)
            step_copy.setdefault("finished_at", None)
            normalized_steps.append(step_copy)

        mission = {
            "mission_id": mission_id,
            "objective": objective,
            "template_id": template_id,
            "status": "pending",
            "current_step_index": 0,
            "steps": normalized_steps,
            "metadata": metadata or {},
            "attempt_count": 0,
            "repair_count": 0,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "last_error": None,
            "result_summary": None,
        }

        missions.append(mission)
        data["missions"] = missions
        self._write_all(data)

        self.event_bus.publish(
            "mission_plan_created",
            mission_id=mission_id,
            payload={"template_id": template_id, "step_count": len(normalized_steps)},
            source="mission_plan_store",
        )
        return mission

    def update_mission(self, mission_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        data = self._read_all()
        missions = data.get("missions", [])
        updated = None

        for idx, mission in enumerate(missions):
            if mission.get("mission_id") == mission_id:
                mission_copy = copy.deepcopy(mission)
                mission_copy.update(copy.deepcopy(patch))
                mission_copy["updated_at"] = utc_now_iso()
                missions[idx] = mission_copy
                updated = mission_copy
                break

        if updated is None:
            raise ValueError(f"Mission not found: {mission_id}")

        data["missions"] = missions
        self._write_all(data)
        return updated

    def replace_mission(self, mission: Dict[str, Any]) -> Dict[str, Any]:
        return self.update_mission(mission["mission_id"], mission)

    def append_step_history(self, record: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.step_history_file, _safe_jsonable(record))

    def list_step_history(self, mission_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if not self.step_history_file.exists():
            return []

        lines = self.step_history_file.read_text(encoding="utf-8").splitlines()
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
