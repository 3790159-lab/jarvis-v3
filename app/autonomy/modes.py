from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .store import JsonStore, utc_now_iso


class ModeManager:
    def __init__(self, store: JsonStore, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.mode_file = self.store.root / "autonomy_mode.json"
        self.approvals_file = self.store.root / "autonomy_approvals.json"
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not self.mode_file.exists():
            self.store.write_json(
                self.mode_file,
                {
                    "mode": "operator_assisted",
                    "autonomous_continuation_enabled": True,
                    "require_approval_for_scheduled": True,
                    "require_approval_for_replan_followup": True,
                    "allow_event_driven_auto_continue": False,
                    "allow_manual_continue_when_paused": False,
                    "updated_at": utc_now_iso(),
                },
            )
        if not self.approvals_file.exists():
            self.store.write_json(self.approvals_file, [])

    def get_mode_config(self) -> Dict[str, Any]:
        self._ensure_files()
        return self.store.read_json(self.mode_file, {})

    def update_mode_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.get_mode_config()
        cfg.update(patch or {})
        cfg["updated_at"] = utc_now_iso()
        self.store.write_json(self.mode_file, cfg)
        self.event_bus.publish(
            "autonomy_mode_updated",
            payload={"mode_config": cfg},
            source="mode_manager",
        )
        return cfg

    def list_approvals(self, mission_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        approvals = self.store.read_json(self.approvals_file, [])
        result = []
        for item in approvals:
            if mission_id and item.get("mission_id") != mission_id:
                continue
            if status and item.get("status") != status:
                continue
            result.append(item)
        return result

    def create_approval(
        self,
        mission_id: str,
        reason: str,
        origin: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        approvals = self.store.read_json(self.approvals_file, [])
        payload = payload or {}

        existing = None
        for item in approvals:
            if (
                item.get("mission_id") == mission_id
                and item.get("reason") == reason
                and item.get("origin") == origin
                and item.get("status") == "pending"
                and item.get("payload", {}).get("job_id") == payload.get("job_id")
            ):
                existing = item
                break

        if existing:
            return existing

        approval = {
            "approval_id": f"apr_{uuid.uuid4().hex[:12]}",
            "mission_id": mission_id,
            "reason": reason,
            "origin": origin,
            "payload": payload,
            "status": "pending",
            "created_at": utc_now_iso(),
            "resolved_at": None,
            "resolution_note": None,
        }
        approvals.append(approval)
        self.store.write_json(self.approvals_file, approvals)

        self.event_bus.publish(
            "approval_requested",
            mission_id=mission_id,
            payload={
                "approval_id": approval["approval_id"],
                "reason": reason,
                "origin": origin,
                "job_id": payload.get("job_id"),
            },
            severity="info",
            source="mode_manager",
        )
        return approval

    def resolve_approval(self, approval_id: str, status: str, note: str = "") -> Dict[str, Any]:
        approvals = self.store.read_json(self.approvals_file, [])
        found = None
        for item in approvals:
            if item.get("approval_id") == approval_id:
                item["status"] = status
                item["resolved_at"] = utc_now_iso()
                item["resolution_note"] = note
                found = item
                break

        if not found:
            raise ValueError(f"Approval not found: {approval_id}")

        self.store.write_json(self.approvals_file, approvals)

        self.event_bus.publish(
            "approval_resolved",
            mission_id=found.get("mission_id"),
            payload={
                "approval_id": approval_id,
                "status": status,
                "note": note,
                "job_id": (found.get("payload") or {}).get("job_id"),
            },
            severity="info",
            source="mode_manager",
        )
        return found

    def evaluate_execution(
        self,
        mission_id: str,
        reason: str,
        origin: str,
        paused: bool = False,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cfg = self.get_mode_config()
        payload = payload or {}

        if paused and not cfg.get("allow_manual_continue_when_paused", False):
            return {
                "allowed": False,
                "action": "blocked_paused",
                "reason": "mission_paused_and_manual_continue_disallowed",
                "approval": None,
            }

        mode = cfg.get("mode", "operator_assisted")

        if origin == "manual":
            return {
                "allowed": True,
                "action": "allow_manual",
                "reason": "manual_execution_allowed",
                "approval": None,
            }

        if mode == "safe_manual":
            approval = self.create_approval(
                mission_id=mission_id,
                reason=reason,
                origin=origin,
                payload={"mode": mode, **payload},
            )
            return {
                "allowed": False,
                "action": "approval_required",
                "reason": "safe_manual_mode_blocks_non_manual_execution",
                "approval": approval,
            }

        if mode == "operator_assisted":
            if origin == "scheduled" and cfg.get("require_approval_for_scheduled", True):
                approval = self.create_approval(
                    mission_id=mission_id,
                    reason=reason,
                    origin=origin,
                    payload={"mode": mode, **payload},
                )
                return {
                    "allowed": False,
                    "action": "approval_required",
                    "reason": "scheduled_execution_requires_approval",
                    "approval": approval,
                }

            if "post_replan:" in reason and cfg.get("require_approval_for_replan_followup", True):
                approval = self.create_approval(
                    mission_id=mission_id,
                    reason=reason,
                    origin=origin,
                    payload={"mode": mode, **payload},
                )
                return {
                    "allowed": False,
                    "action": "approval_required",
                    "reason": "replan_followup_requires_approval",
                    "approval": approval,
                }

            if origin == "event" and not cfg.get("allow_event_driven_auto_continue", False):
                approval = self.create_approval(
                    mission_id=mission_id,
                    reason=reason,
                    origin=origin,
                    payload={"mode": mode, **payload},
                )
                return {
                    "allowed": False,
                    "action": "approval_required",
                    "reason": "event_driven_auto_continue_disabled",
                    "approval": approval,
                }

            return {
                "allowed": True,
                "action": "allow_operator_assisted",
                "reason": "operator_assisted_execution_allowed",
                "approval": None,
            }

        if mode == "autonomous_limited":
            if not cfg.get("autonomous_continuation_enabled", True):
                approval = self.create_approval(
                    mission_id=mission_id,
                    reason=reason,
                    origin=origin,
                    payload={"mode": mode, **payload},
                )
                return {
                    "allowed": False,
                    "action": "approval_required",
                    "reason": "autonomous_continuation_disabled",
                    "approval": approval,
                }

            return {
                "allowed": True,
                "action": "allow_autonomous_limited",
                "reason": "autonomous_limited_execution_allowed",
                "approval": None,
            }

        approval = self.create_approval(
            mission_id=mission_id,
            reason=reason,
            origin=origin,
            payload={"mode": mode, **payload},
        )
        return {
            "allowed": False,
            "action": "approval_required",
            "reason": "unknown_mode_fell_back_to_approval",
            "approval": approval,
        }
