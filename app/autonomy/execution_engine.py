from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, List, Optional

from .store import utc_now_iso


def _safe_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_jsonable(v) for v in value]
    return f"<nonserializable:{type(value).__name__}>"


class ExecutionEngine:
    def __init__(
        self,
        store: Any,
        event_bus: Any,
        tool_executor: Any,
        workflow_manager: Any,
        graph_manager: Any,
        repair_manager: Any,
        guard_manager: Any,
        mode_manager: Any,
        autonomy_getter: Callable[[], Dict[str, Any]],
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self.tool_executor = tool_executor
        self.workflow_manager = workflow_manager
        self.graph_manager = graph_manager
        self.repair_manager = repair_manager
        self.guard_manager = guard_manager
        self.mode_manager = mode_manager
        self.autonomy_getter = autonomy_getter
        self.executions_file = self.store.root / "engine_executions.jsonl"

    def _append_execution(self, record: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.executions_file, _safe_jsonable(record))

    def list_executions(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.executions_file.exists():
            return []

        lines = self.executions_file.read_text(encoding="utf-8").splitlines()
        result: List[Dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                result.append(json.loads(line))
            except Exception:
                continue
            if len(result) >= limit:
                break
        result.reverse()
        return result

    async def execute_step(self, step: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = context or {}
        execution_id = f"eng_{uuid.uuid4().hex[:12]}"
        started_at = utc_now_iso()

        mission_id = step.get("mission_id") or context.get("mission_id")
        mode_check = None
        guard_check = None

        self.event_bus.publish(
            "engine_step_started",
            mission_id=mission_id,
            payload={"execution_id": execution_id, "step_type": step.get("type"), "action": step.get("action")},
            source="execution_engine",
        )

        try:
            if mission_id:
                guard_check = self.guard_manager.assess_mission(mission_id)
                if not guard_check.get("allowed"):
                    record = {
                        "execution_id": execution_id,
                        "mission_id": mission_id,
                        "status": "blocked_by_guard",
                        "started_at": started_at,
                        "finished_at": utc_now_iso(),
                        "step": step,
                        "guard_check": guard_check,
                    }
                    self._append_execution(record)
                    return record

                paused = False
                autonomy = self.autonomy_getter()
                runtime = autonomy["store"].get_runtime()
                bucket = runtime.get(mission_id, {}) if isinstance(runtime, dict) else {}
                paused = bool(bucket.get("paused"))

                origin = str(step.get("origin", "engine"))
                mode_check = self.mode_manager.evaluate_execution(
                    mission_id=mission_id,
                    reason=str(step.get("reason", step.get("action", "engine_step"))),
                    origin=origin if origin in ("manual", "scheduled", "event") else "manual",
                    paused=paused,
                    payload={"engine_execution_id": execution_id},
                )

                if not mode_check.get("allowed"):
                    record = {
                        "execution_id": execution_id,
                        "mission_id": mission_id,
                        "status": "blocked_by_mode",
                        "started_at": started_at,
                        "finished_at": utc_now_iso(),
                        "step": step,
                        "mode_check": mode_check,
                    }
                    self._append_execution(record)
                    return record

            execute_result = await self._dispatch(step)
            verified = self._verify(step.get("verify"), execute_result)
            repair_result = None
            final_status = "completed"

            if not verified and step.get("repair"):
                repair_payload = dict(step.get("repair_payload") or {})
                if mission_id and "mission_id" not in repair_payload:
                    repair_payload["mission_id"] = mission_id

                repair_result = await self.repair_manager.execute_repair(
                    repair_id=step["repair"],
                    payload=repair_payload,
                    requested_by="execution_engine",
                )

                retry_after_repair = bool(step.get("retry_after_repair", True))
                if retry_after_repair:
                    execute_result_retry = await self._dispatch(step)
                    verified_retry = self._verify(step.get("verify"), execute_result_retry)
                    execute_result = {
                        "initial": execute_result,
                        "after_repair": execute_result_retry,
                    }
                    verified = verified_retry

                final_status = "repaired_completed" if verified else "repaired_failed"
            else:
                final_status = "completed" if verified else "failed_verification"

            record = {
                "execution_id": execution_id,
                "mission_id": mission_id,
                "status": final_status,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "step": step,
                "context": context,
                "guard_check": guard_check,
                "mode_check": mode_check,
                "result": execute_result,
                "verified": verified,
                "repair_result": repair_result,
            }
            self._append_execution(record)

            self.event_bus.publish(
                "engine_step_completed",
                mission_id=mission_id,
                payload={"execution_id": execution_id, "status": final_status, "verified": verified},
                source="execution_engine",
            )
            return record

        except Exception as exc:
            record = {
                "execution_id": execution_id,
                "mission_id": mission_id,
                "status": "error",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "step": step,
                "context": context,
                "error": str(exc),
            }
            self._append_execution(record)

            self.event_bus.publish(
                "engine_step_failed",
                mission_id=mission_id,
                payload={"execution_id": execution_id, "error": str(exc)},
                severity="warning",
                source="execution_engine",
            )
            return record

    async def _dispatch(self, step: Dict[str, Any]) -> Dict[str, Any]:
        step_type = step["type"]
        action = step["action"]
        payload = step.get("payload", {})
        requested_by = step.get("requested_by", "execution_engine")

        if step_type == "tool":
            return await self.tool_executor.execute(
                tool_id=action,
                payload=payload,
                requested_by=requested_by,
            )

        if step_type == "workflow":
            return await self.workflow_manager.execute_workflow(
                workflow_id=action,
                payload=payload,
                requested_by=requested_by,
            )

        if step_type == "graph":
            return await self.graph_manager.execute_graph(
                graph_id=action,
                payload=payload,
                requested_by=requested_by,
            )

        raise ValueError(f"Unknown step type: {step_type}")

    def _verify(self, rule: Optional[Dict[str, Any]], result: Dict[str, Any]) -> bool:
        if not rule:
            status = result.get("status")
            if status in ("completed", "repaired_completed"):
                return True
            inner = result.get("result")
            if isinstance(inner, dict) and inner.get("ok") is True:
                return True
            return status != "failed"

        rule_type = rule.get("type")

        if rule_type == "status_in":
            return result.get("status") in list(rule.get("values", []))

        if rule_type == "field_equals":
            field = str(rule["field"])
            expected = rule.get("value")
            return result.get(field) == expected

        if rule_type == "inner_ok":
            inner = result.get("result")
            return isinstance(inner, dict) and inner.get("ok") is True

        if rule_type == "contains_text":
            field = str(rule.get("field", "result"))
            needle = str(rule["value"])
            hay = result.get(field)
            if isinstance(hay, dict):
                hay = json.dumps(hay, ensure_ascii=False)
            return needle in str(hay)

        return True
