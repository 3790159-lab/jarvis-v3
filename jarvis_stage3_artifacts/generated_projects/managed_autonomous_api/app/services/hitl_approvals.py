from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HITLApprovalService:
    MAX_APPROVALS = 500
    MAX_AUDIT_ITEMS = 50
    ALLOWED_DECISIONS = {"approve", "reject"}
    ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.base_dir / "approvals_store.json"
        self._lock = threading.RLock()

    def _default_store(self) -> Dict[str, Any]:
        return {
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "approvals": []
        }

    def _read_store(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            store = self._default_store()
            self._write_store(store)
            return store

        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Store must be a JSON object")
            if "approvals" not in data or not isinstance(data["approvals"], list):
                data["approvals"] = []
            if "created_at" not in data:
                data["created_at"] = utc_now()
            if "updated_at" not in data:
                data["updated_at"] = utc_now()
            return data
        except Exception:
            store = self._default_store()
            self._write_store(store)
            return store

    def _write_store(self, store: Dict[str, Any]) -> None:
        store["updated_at"] = utc_now()
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.store_path)

    def _normalize_risk(self, risk_level: str) -> str:
        risk = (risk_level or "").strip().lower()
        return risk if risk in self.ALLOWED_RISK_LEVELS else "medium"

    def _trim_audit(self, item: Dict[str, Any]) -> None:
        audit = item.get("audit_trail", [])
        if len(audit) > self.MAX_AUDIT_ITEMS:
            item["audit_trail"] = audit[-self.MAX_AUDIT_ITEMS:]

    def health(self) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            pending = len([x for x in store["approvals"] if x.get("status") == "pending"])
            decided = len([x for x in store["approvals"] if x.get("status") in {"approved", "rejected"}])
            return {
                "status": "healthy",
                "store_path": str(self.store_path),
                "total_approvals": len(store["approvals"]),
                "pending_count": pending,
                "decided_count": decided,
                "updated_at": store["updated_at"],
            }

    def _find_duplicate_pending(
        self,
        approvals: List[Dict[str, Any]],
        mission_id: str,
        action_type: str,
        proposed_action: str,
    ) -> Dict[str, Any] | None:
        for item in reversed(approvals):
            if (
                item.get("status") == "pending"
                and item.get("mission_id") == mission_id
                and item.get("action_type") == action_type
                and item.get("proposed_action") == proposed_action
            ):
                return item
        return None

    def create_approval(
        self,
        mission_id: str,
        action_type: str,
        proposed_action: str,
        reason: str,
        risk_level: str,
        agent_id: str = "",
        agent_score: float = 0.0,
        payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()

            duplicate = self._find_duplicate_pending(
                store["approvals"],
                mission_id=mission_id,
                action_type=action_type,
                proposed_action=proposed_action,
            )
            if duplicate:
                duplicate["audit_trail"].append({
                    "ts": utc_now(),
                    "event": "duplicate_reuse",
                    "message": "Existing pending approval returned instead of creating a duplicate"
                })
                self._trim_audit(duplicate)
                self._write_store(store)
                return deepcopy(duplicate)

            approval = {
                "approval_id": f"approval_{uuid.uuid4().hex[:10]}",
                "mission_id": mission_id.strip(),
                "action_type": action_type.strip(),
                "proposed_action": proposed_action.strip(),
                "reason": reason.strip(),
                "risk_level": self._normalize_risk(risk_level),
                "agent_id": agent_id.strip(),
                "agent_score": round(float(agent_score), 2),
                "payload": payload or {},
                "status": "pending",
                "operator_note": "",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "decision_at": None,
                "audit_trail": [
                    {
                        "ts": utc_now(),
                        "event": "created",
                        "message": "Approval request created"
                    }
                ]
            }

            store["approvals"].append(approval)
            if len(store["approvals"]) > self.MAX_APPROVALS:
                store["approvals"] = store["approvals"][-self.MAX_APPROVALS:]

            self._write_store(store)
            return deepcopy(approval)

    def list_approvals(self, status: str | None = None) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            items: List[Dict[str, Any]] = deepcopy(store["approvals"])

            if status:
                status = status.strip().lower()
                items = [x for x in items if x.get("status") == status]

            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return {
                "count": len(items),
                "items": items,
            }

    def get_approval(self, approval_id: str) -> Dict[str, Any]:
        with self._lock:
            store = self._read_store()
            for item in store["approvals"]:
                if item.get("approval_id") == approval_id:
                    return deepcopy(item)
            return {
                "found": False,
                "approval_id": approval_id,
                "message": "Approval request not found",
            }

    def decide(self, approval_id: str, decision: str, operator_note: str = "") -> Dict[str, Any]:
        decision = (decision or "").strip().lower()
        if decision not in self.ALLOWED_DECISIONS:
            raise ValueError(f"Unsupported decision: {decision}")

        with self._lock:
            store = self._read_store()
            for item in store["approvals"]:
                if item.get("approval_id") != approval_id:
                    continue

                if item.get("status") != "pending":
                    item["audit_trail"].append({
                        "ts": utc_now(),
                        "event": "decision_ignored",
                        "message": f"Decision ignored because approval already resolved as {item.get('status')}"
                    })
                    self._trim_audit(item)
                    self._write_store(store)
                    return deepcopy(item)

                item["status"] = "approved" if decision == "approve" else "rejected"
                item["operator_note"] = operator_note.strip()
                item["decision_at"] = utc_now()
                item["updated_at"] = utc_now()
                item["audit_trail"].append({
                    "ts": utc_now(),
                    "event": "decided",
                    "message": f"Approval {item['status']}",
                    "decision": decision,
                    "operator_note": item["operator_note"],
                })
                self._trim_audit(item)

                self._write_store(store)
                return deepcopy(item)

            return {
                "found": False,
                "approval_id": approval_id,
                "message": "Approval request not found",
            }


service = HITLApprovalService(Path(__file__).resolve().parents[2] / "artifacts" / "hitl_approvals")
