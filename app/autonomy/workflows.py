from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from .store import utc_now_iso


class WorkflowManager:
    def __init__(self, store: Any, event_bus: Any, tool_executor: Any, chain_manager: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.tool_executor = tool_executor
        self.chain_manager = chain_manager
        self.workflows_file = self.store.root / "workflow_executions.jsonl"

    def _append_workflow(self, record: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.workflows_file, record)

    def list_workflows(self, limit: int = 30, workflow_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.workflows_file.exists():
            return []

        lines = self.workflows_file.read_text(encoding="utf-8").splitlines()
        result: List[Dict[str, Any]] = []

        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if workflow_id and item.get("workflow_id") != workflow_id:
                continue
            result.append(item)
            if len(result) >= limit:
                break

        result.reverse()
        return result

    def list_templates(self) -> List[Dict[str, Any]]:
        return [
            {
                "workflow_id": "file_patch_verify",
                "description": "Patch a file and verify the final content.",
                "required_inputs": ["path", "search", "replace"],
            },
            {
                "workflow_id": "json_write_verify",
                "description": "Write JSON and verify by reading it back.",
                "required_inputs": ["path", "data"],
            },
            {
                "workflow_id": "inspect_repair_retest_port",
                "description": "Inspect port, optionally kill stale owner, then inspect again.",
                "required_inputs": ["port"],
            },
            {
                "workflow_id": "mission_safe_continue",
                "description": "Run preflight checks and continue mission safely.",
                "required_inputs": ["mission_id"],
            },
        ]

    async def execute_workflow(self, workflow_id: str, payload: Dict[str, Any], requested_by: str = "workflow_api") -> Dict[str, Any]:
        execution_id = f"wfx_{uuid.uuid4().hex[:12]}"
        started_at = utc_now_iso()

        self.event_bus.publish(
            "workflow_execution_started",
            payload={"execution_id": execution_id, "workflow_id": workflow_id, "requested_by": requested_by},
            source="workflow_manager",
        )

        try:
            if workflow_id == "file_patch_verify":
                result = await self._workflow_file_patch_verify(payload, requested_by)
            elif workflow_id == "json_write_verify":
                result = await self._workflow_json_write_verify(payload, requested_by)
            elif workflow_id == "inspect_repair_retest_port":
                result = await self._workflow_inspect_repair_retest_port(payload, requested_by)
            elif workflow_id == "mission_safe_continue":
                result = await self._workflow_mission_safe_continue(payload, requested_by)
            else:
                raise ValueError(f"Unknown workflow template: {workflow_id}")

            record = {
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "requested_by": requested_by,
                "status": "completed" if result.get("ok", False) else "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": payload,
                "result": result,
            }
            self._append_workflow(record)

            self.event_bus.publish(
                "workflow_execution_completed",
                payload={
                    "execution_id": execution_id,
                    "workflow_id": workflow_id,
                    "ok": result.get("ok", False),
                },
                source="workflow_manager",
            )
            return record
        except Exception as exc:
            record = {
                "execution_id": execution_id,
                "workflow_id": workflow_id,
                "requested_by": requested_by,
                "status": "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": payload,
                "error": str(exc),
            }
            self._append_workflow(record)

            self.event_bus.publish(
                "workflow_execution_failed",
                payload={"execution_id": execution_id, "workflow_id": workflow_id, "error": str(exc)},
                severity="warning",
                source="workflow_manager",
            )
            return record

    async def _workflow_file_patch_verify(self, payload: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
        path = payload["path"]
        search = payload["search"]
        replace = payload["replace"]

        patch_record = await self.tool_executor.execute(
            tool_id="file_patch_text",
            payload={"path": path, "search": search, "replace": replace},
            requested_by=requested_by,
        )
        if patch_record.get("status") != "completed":
            return {"ok": False, "phase": "patch", "record": patch_record}

        read_record = await self.tool_executor.execute(
            tool_id="file_read",
            payload={"path": path},
            requested_by=requested_by,
        )
        if read_record.get("status") != "completed":
            return {"ok": False, "phase": "read_back", "record": read_record}

        content = (read_record.get("result") or {}).get("content", "")
        verified = replace in content and search not in content

        return {
            "ok": verified,
            "patch_record": patch_record,
            "read_record": read_record,
            "verification": {
                "replace_present": replace in content,
                "search_absent": search not in content,
            },
        }

    async def _workflow_json_write_verify(self, payload: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
        path = payload["path"]
        data = payload["data"]

        write_record = await self.tool_executor.execute(
            tool_id="json_write",
            payload={"path": path, "data": data},
            requested_by=requested_by,
        )
        if write_record.get("status") != "completed":
            return {"ok": False, "phase": "write", "record": write_record}

        read_record = await self.tool_executor.execute(
            tool_id="json_read",
            payload={"path": path},
            requested_by=requested_by,
        )
        if read_record.get("status") != "completed":
            return {"ok": False, "phase": "read_back", "record": read_record}

        read_data = (read_record.get("result") or {}).get("data")
        return {
            "ok": read_data == data,
            "write_record": write_record,
            "read_record": read_record,
            "verification": {"roundtrip_equal": read_data == data},
        }

    async def _workflow_inspect_repair_retest_port(self, payload: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
        port = int(payload["port"])
        allow_kill = bool(payload.get("allow_kill", False))

        inspect_before = await self.tool_executor.execute(
            tool_id="process_inspect_port",
            payload={"port": port},
            requested_by=requested_by,
        )

        repair_record = None
        if allow_kill:
            repair_record = await self.tool_executor.execute(
                tool_id="process_kill_port",
                payload={"port": port},
                requested_by=requested_by,
            )

        inspect_after = await self.tool_executor.execute(
            tool_id="process_inspect_port",
            payload={"port": port},
            requested_by=requested_by,
        )

        return {
            "ok": inspect_before.get("status") == "completed" and inspect_after.get("status") == "completed",
            "inspect_before": inspect_before,
            "repair_record": repair_record,
            "inspect_after": inspect_after,
        }

    async def _workflow_mission_safe_continue(self, payload: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
        mission_id = payload["mission_id"]

        inspect_record = await self.tool_executor.execute(
            tool_id="process_inspect_port",
            payload={"port": 8015},
            requested_by=requested_by,
        )
        if inspect_record.get("status") != "completed":
            return {"ok": False, "phase": "preflight", "record": inspect_record}

        continue_record = await self.tool_executor.execute(
            tool_id="mission_continue",
            payload={"mission_id": mission_id, "reason": "workflow_safe_continue"},
            requested_by=requested_by,
        )

        verified = False
        if continue_record.get("status") == "completed":
            result = continue_record.get("result") or {}
            verified = bool(result.get("ok"))

        return {
            "ok": verified,
            "preflight": inspect_record,
            "continue_record": continue_record,
        }
