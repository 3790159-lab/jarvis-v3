from __future__ import annotations

from typing import Any, Dict
from .store import utc_now_iso


class RecoveryPolicyManager:
    def __init__(self, store: Any, event_bus: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.policy_file = self.store.root / "recovery_policy.json"
        self._ensure_policy()

    def _ensure_policy(self) -> None:
        if not self.policy_file.exists():
            self.store.write_json(
                self.policy_file,
                {
                    "max_step_attempts_default": 2,
                    "max_step_repairs_default": 1,
                    "max_mission_repairs_total": 3,
                    "max_mission_failures_before_operator": 2,
                    "cooldown_seconds_after_repair": 0,
                    "escalate_on_blocked_by_mode": True,
                    "escalate_on_guard_block": True,
                    "updated_at": utc_now_iso(),
                },
            )

    def get_policy(self) -> Dict[str, Any]:
        self._ensure_policy()
        return self.store.read_json(self.policy_file, {})

    def update_policy(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        policy = self.get_policy()
        policy.update(patch or {})
        policy["updated_at"] = utc_now_iso()
        self.store.write_json(self.policy_file, policy)
        self.event_bus.publish(
            "recovery_policy_updated",
            payload={"policy": policy},
            source="recovery_policy_manager",
        )
        return policy
