from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, List, Optional

from .store import utc_now_iso


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        safe = {}
        for k, v in value.items():
            try:
                safe[str(k)] = _json_safe(v)
            except Exception:
                safe[str(k)] = f"<nonserializable:{type(v).__name__}>"
        return safe

    if isinstance(value, (list, tuple, set)):
        safe_list = []
        for item in value:
            try:
                safe_list.append(_json_safe(item))
            except Exception:
                safe_list.append(f"<nonserializable:{type(item).__name__}>")
        return safe_list

    return f"<nonserializable:{type(value).__name__}>"


class RepairLoopManager:
    def __init__(
        self,
        store: Any,
        event_bus: Any,
        workflow_manager: Any,
        maintenance_manager: Any,
        autonomy_getter: Callable[[], Dict[str, Any]],
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self.workflow_manager = workflow_manager
        self.maintenance_manager = maintenance_manager
        self.autonomy_getter = autonomy_getter
        self.repair_runs_file = self.store.root / "repair_loop_runs.jsonl"

    def _append_run(self, record: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.repair_runs_file, _json_safe(record))

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.repair_runs_file.exists():
            return []

        lines = self.repair_runs_file.read_text(encoding="utf-8").splitlines()
        result = []
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

    def list_templates(self) -> List[Dict[str, Any]]:
        return [
            {
                "repair_id": "cleanup_and_unpause",
                "description": "Cleanup queue/runtime inconsistencies and attempt safe unpause.",
                "required_inputs": ["mission_id"],
            },
            {
                "repair_id": "repair_port_state",
                "description": "Run inspect/repair/reinspect workflow for a port.",
                "required_inputs": ["port"],
            },
        ]

    async def execute_repair(
        self,
        repair_id: str,
        payload: Dict[str, Any],
        requested_by: str = "repair_api",
    ) -> Dict[str, Any]:
        run_id = f"rpx_{uuid.uuid4().hex[:12]}"
        started_at = utc_now_iso()

        self.event_bus.publish(
            "repair_loop_started",
            payload={"run_id": run_id, "repair_id": repair_id, "requested_by": requested_by},
            source="repair_loop_manager",
        )

        safe_payload = _json_safe(payload)

        try:
            if repair_id == "cleanup_and_unpause":
                result = await self._repair_cleanup_and_unpause(payload)
            elif repair_id == "repair_port_state":
                result = await self._repair_port_state(payload, requested_by)
            else:
                raise ValueError(f"Unknown repair template: {repair_id}")

            record = {
                "run_id": run_id,
                "repair_id": repair_id,
                "requested_by": requested_by,
                "status": "completed" if result.get("ok", False) else "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": safe_payload,
                "result": _json_safe(result),
            }
            self._append_run(record)

            self.event_bus.publish(
                "repair_loop_completed",
                payload={"run_id": run_id, "repair_id": repair_id, "ok": result.get("ok", False)},
                source="repair_loop_manager",
            )
            return record
        except Exception as exc:
            record = {
                "run_id": run_id,
                "repair_id": repair_id,
                "requested_by": requested_by,
                "status": "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": safe_payload,
                "error": str(exc),
            }
            self._append_run(record)

            self.event_bus.publish(
                "repair_loop_failed",
                payload={"run_id": run_id, "repair_id": repair_id, "error": str(exc)},
                severity="warning",
                source="repair_loop_manager",
            )
            return record

    async def _repair_cleanup_and_unpause(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mission_id = payload["mission_id"]
        autonomy = self.autonomy_getter()

        cleanup_legacy = self.maintenance_manager.cleanup_legacy_pending_jobs()
        cleanup_approvals = self.maintenance_manager.cleanup_orphan_approvals()
        cleanup_runtime = self.maintenance_manager.cleanup_stale_runtime()
        auto_unpause = self.maintenance_manager.try_auto_unpause(
            guard_manager=autonomy["guard_manager"],
            scheduler=autonomy["scheduler"],
            mission_id=mission_id,
        )

        return {
            "ok": auto_unpause.get("action") in ("resumed", "noop"),
            "cleanup_legacy": cleanup_legacy,
            "cleanup_approvals": cleanup_approvals,
            "cleanup_runtime": cleanup_runtime,
            "auto_unpause": auto_unpause,
        }

    async def _repair_port_state(self, payload: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
        port = int(payload["port"])
        workflow = await self.workflow_manager.execute_workflow(
            workflow_id="inspect_repair_retest_port",
            payload={"port": port, "allow_kill": bool(payload.get("allow_kill", False))},
            requested_by=requested_by,
        )
        return {
            "ok": workflow.get("status") == "completed",
            "workflow": workflow,
        }
