from __future__ import annotations

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


class OperatorEscalationManager:
    def __init__(self, store: Any, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.file = self.store.root / "operator_escalations.jsonl"

    def _append(self, record: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.file, _safe_jsonable(record))

    def list_escalations(self, limit: int = 50, mission_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.file.exists():
            return []
        lines = self.file.read_text(encoding="utf-8").splitlines()
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

    def create_escalation(
        self,
        mission_id: str,
        reason: str,
        summary: Dict[str, Any],
        recommended_action: str,
    ) -> Dict[str, Any]:
        record = {
            "escalation_id": f"esc_{uuid.uuid4().hex[:12]}",
            "mission_id": mission_id,
            "reason": reason,
            "summary": _safe_jsonable(summary),
            "recommended_action": recommended_action,
            "created_at": utc_now_iso(),
        }
        self._append(record)
        self.event_bus.publish(
            "operator_escalation_created",
            mission_id=mission_id,
            payload={
                "escalation_id": record["escalation_id"],
                "reason": reason,
                "recommended_action": recommended_action,
            },
            severity="warning",
            source="operator_escalation_manager",
        )
        return record
