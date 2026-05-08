from __future__ import annotations

from typing import Any, Dict


def try_extract_objective_from_mission_result(mission_result: Dict[str, Any]) -> str:
    for key in ("objective", "goal", "prompt", "summary"):
        value = mission_result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def should_attempt_memory_write(mission_result: Dict[str, Any]) -> bool:
    mission_id = mission_result.get("mission_id") or mission_result.get("id")
    return bool(mission_id)
