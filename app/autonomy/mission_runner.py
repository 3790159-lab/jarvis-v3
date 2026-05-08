from __future__ import annotations

import copy
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


class MissionRunner:
    TERMINAL_STATUSES = {"completed", "failed", "blocked", "needs_operator"}

    def __init__(
        self,
        plan_store: Any,
        template_manager: Any,
        execution_engine: Any,
        repair_manager: Any,
        guard_manager: Any,
        mode_manager: Any,
        event_bus: Any,
        mission_registry: Any,
        recovery_policy_manager: Any,
        operator_escalation_manager: Any,
    ) -> None:
        self.plan_store = plan_store
        self.template_manager = template_manager
        self.execution_engine = execution_engine
        self.repair_manager = repair_manager
        self.guard_manager = guard_manager
        self.mode_manager = mode_manager
        self.event_bus = event_bus
        self.mission_registry = mission_registry
        self.recovery_policy_manager = recovery_policy_manager
        self.operator_escalation_manager = operator_escalation_manager

    def list_templates(self) -> List[Dict[str, Any]]:
        return self.template_manager.list_templates()

    def create_mission_from_template(
        self,
        template_id: str,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        built = self.template_manager.build_template(template_id, payload)
        return self.plan_store.create_mission(
            objective=built["objective"],
            template_id=template_id,
            steps=built["steps"],
            metadata=metadata or {"template_payload": payload},
        )

    def list_missions(self) -> List[Dict[str, Any]]:
        return self.plan_store.list_missions()

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        return self.plan_store.get_mission(mission_id)

    async def run_mission(self, mission_id: str, max_steps: int = 50) -> Dict[str, Any]:
        mission = self.plan_store.get_mission(mission_id)
        if not mission:
            raise ValueError(f"Mission not found: {mission_id}")

        if mission.get("status") in self.TERMINAL_STATUSES:
            return {"ok": mission.get("status") == "completed", "mission": mission, "steps_executed": 0}

        policy = self.recovery_policy_manager.get_policy()
        default_max_attempts = int(policy.get("max_step_attempts_default", 2))
        default_max_repairs = int(policy.get("max_step_repairs_default", 1))
        max_mission_repairs_total = int(policy.get("max_mission_repairs_total", 3))

        if mission.get("started_at") is None:
            mission["started_at"] = utc_now_iso()
        mission["status"] = "running"
        mission["updated_at"] = utc_now_iso()
        mission["attempt_count"] = int(mission.get("attempt_count", 0)) + 1
        mission = self.plan_store.replace_mission(mission)

        self.event_bus.publish(
            "mission_runner_started",
            mission_id=mission_id,
            payload={"template_id": mission.get("template_id")},
            source="mission_runner",
        )

        steps_executed = 0
        while steps_executed < max_steps:
            mission = self.plan_store.get_mission(mission_id)
            if not mission:
                raise ValueError(f"Mission disappeared: {mission_id}")

            if mission.get("status") in self.TERMINAL_STATUSES:
                break

            current_index = int(mission.get("current_step_index", 0))
            steps = mission.get("steps", [])

            if current_index >= len(steps):
                mission["status"] = "completed"
                mission["finished_at"] = utc_now_iso()
                mission["result_summary"] = {"completed_steps": len(steps), "message": "All mission steps completed"}
                mission = self.plan_store.replace_mission(mission)
                self.event_bus.publish(
                    "mission_runner_completed",
                    mission_id=mission_id,
                    payload={"completed_steps": len(steps)},
                    source="mission_runner",
                )
                break

            step = copy.deepcopy(steps[current_index])
            step.setdefault("max_attempts", default_max_attempts)
            step.setdefault("max_repairs", default_max_repairs)
            step.setdefault("on_failure", "fail")
            step["mission_id"] = mission_id
            step["origin"] = "manual"
            step["reason"] = f"mission_step:{mission_id}:{step.get('step_id')}"

            step["status"] = "running"
            step["started_at"] = utc_now_iso()
            steps[current_index] = step
            mission["steps"] = steps
            mission["updated_at"] = utc_now_iso()
            mission = self.plan_store.replace_mission(mission)

            engine_result = await self.execution_engine.execute_step(
                step=step,
                context={"mission_id": mission_id, "step_id": step.get("step_id")},
            )

            self.plan_store.append_step_history(
                {
                    "mission_id": mission_id,
                    "step_id": step.get("step_id"),
                    "step_index": current_index,
                    "recorded_at": utc_now_iso(),
                    "engine_result": _safe_jsonable(engine_result),
                }
            )

            mission = self.plan_store.get_mission(mission_id)
            steps = mission.get("steps", [])
            step = steps[current_index]
            status = engine_result.get("status")

            if status in ("completed", "repaired_completed"):
                step["status"] = "completed"
                step["finished_at"] = utc_now_iso()
                step["last_error"] = None
                steps[current_index] = step
                mission["steps"] = steps
                mission["current_step_index"] = current_index + 1
                mission["updated_at"] = utc_now_iso()
                mission["last_error"] = None
                mission = self.plan_store.replace_mission(mission)
                steps_executed += 1
                continue

            if status == "blocked_by_guard":
                mission["status"] = "blocked" if policy.get("escalate_on_guard_block", True) else "failed"
                mission["last_error"] = "Blocked by guard"
                mission["finished_at"] = utc_now_iso()
                mission["result_summary"] = {"reason": "blocked_by_guard", "engine_result": _safe_jsonable(engine_result)}
                self.operator_escalation_manager.create_escalation(
                    mission_id=mission_id,
                    reason="blocked_by_guard",
                    summary={"engine_result": _safe_jsonable(engine_result)},
                    recommended_action="Review mission load, pending jobs, and guard thresholds",
                )
                step["status"] = "blocked"
                step["finished_at"] = utc_now_iso()
                step["last_error"] = "Blocked by guard"
                steps[current_index] = step
                mission["steps"] = steps
                mission = self.plan_store.replace_mission(mission)
                break

            if status == "blocked_by_mode":
                mission["status"] = "needs_operator" if policy.get("escalate_on_blocked_by_mode", True) else "blocked"
                mission["last_error"] = "Blocked by mode"
                mission["finished_at"] = utc_now_iso()
                mission["result_summary"] = {"reason": "blocked_by_mode", "engine_result": _safe_jsonable(engine_result)}
                self.operator_escalation_manager.create_escalation(
                    mission_id=mission_id,
                    reason="blocked_by_mode",
                    summary={"engine_result": _safe_jsonable(engine_result)},
                    recommended_action="Approve or switch mode/policy for this mission",
                )
                step["status"] = "blocked"
                step["finished_at"] = utc_now_iso()
                step["last_error"] = "Blocked by mode"
                steps[current_index] = step
                mission["steps"] = steps
                mission = self.plan_store.replace_mission(mission)
                break

            step["attempt_count"] = int(step.get("attempt_count", 0)) + 1
            step["last_error"] = status
            step["finished_at"] = utc_now_iso()

            needs_repair = status in ("failed_verification", "error", "repaired_failed")
            repair_name = step.get("repair")

            if (
                needs_repair
                and repair_name
                and int(step.get("repair_count", 0)) < int(step.get("max_repairs", default_max_repairs))
                and int(mission.get("repair_count", 0)) < max_mission_repairs_total
            ):
                repair_payload = dict(step.get("repair_payload") or {})
                repair_payload.setdefault("mission_id", mission_id)
                repair_result = await self.repair_manager.execute_repair(
                    repair_id=repair_name,
                    payload=repair_payload,
                    requested_by="mission_runner",
                )
                step["repair_count"] = int(step.get("repair_count", 0)) + 1
                mission["repair_count"] = int(mission.get("repair_count", 0)) + 1

                if repair_result.get("status") == "completed" and int(step.get("attempt_count", 0)) < int(step.get("max_attempts", default_max_attempts)):
                    step["status"] = "pending"
                    step["started_at"] = None
                    step["finished_at"] = None
                    steps[current_index] = step
                    mission["steps"] = steps
                    mission["updated_at"] = utc_now_iso()
                    mission["last_error"] = "Step repaired and queued for retry"
                    mission = self.plan_store.replace_mission(mission)
                    steps_executed += 1
                    continue

            if int(step.get("attempt_count", 0)) < int(step.get("max_attempts", default_max_attempts)):
                step["status"] = "pending"
                step["started_at"] = None
                step["finished_at"] = None
                steps[current_index] = step
                mission["steps"] = steps
                mission["updated_at"] = utc_now_iso()
                mission["last_error"] = f"Retry queued after status: {status}"
                mission = self.plan_store.replace_mission(mission)
                steps_executed += 1
                continue

            failure_policy = step.get("on_failure", "fail")
            if failure_policy == "needs_operator":
                mission["status"] = "needs_operator"
                self.operator_escalation_manager.create_escalation(
                    mission_id=mission_id,
                    reason="step_failed_needs_operator",
                    summary={"step_id": step.get("step_id"), "engine_result": _safe_jsonable(engine_result)},
                    recommended_action="Inspect step failure and choose manual recovery",
                )
            elif failure_policy == "blocked":
                mission["status"] = "blocked"
            else:
                mission["status"] = "failed"

            step["status"] = "failed"
            steps[current_index] = step
            mission["steps"] = steps
            mission["finished_at"] = utc_now_iso()
            mission["updated_at"] = utc_now_iso()
            mission["last_error"] = f"Step failed permanently: {status}"
            mission["result_summary"] = {
                "failed_step_id": step.get("step_id"),
                "failed_step_index": current_index,
                "engine_result": _safe_jsonable(engine_result),
            }
            mission = self.plan_store.replace_mission(mission)
            break

        mission = self.plan_store.get_mission(mission_id)
        return {"ok": mission.get("status") == "completed", "mission": mission, "steps_executed": steps_executed}
