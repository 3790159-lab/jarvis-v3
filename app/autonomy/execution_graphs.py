from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Set

from .store import utc_now_iso


class ExecutionGraphManager:
    def __init__(self, store: Any, event_bus: Any, tool_executor: Any, workflow_manager: Any) -> None:
        self.store = store
        self.event_bus = event_bus
        self.tool_executor = tool_executor
        self.workflow_manager = workflow_manager
        self.graph_runs_file = self.store.root / "execution_graph_runs.jsonl"

    def _append_run(self, record: Dict[str, Any]) -> None:
        self.store.append_jsonl(self.graph_runs_file, record)

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.graph_runs_file.exists():
            return []
        lines = self.graph_runs_file.read_text(encoding="utf-8").splitlines()
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
                "graph_id": "repairable_file_patch_graph",
                "description": "Read -> patch -> verify -> optional workflow repair.",
            },
            {
                "graph_id": "mission_safe_resume_graph",
                "description": "Inspect -> workflow safe continue -> verify.",
            },
        ]

    async def execute_graph(self, graph_id: str, payload: Dict[str, Any], requested_by: str = "graph_api") -> Dict[str, Any]:
        run_id = f"gex_{uuid.uuid4().hex[:12]}"
        started_at = utc_now_iso()

        self.event_bus.publish(
            "graph_execution_started",
            payload={"run_id": run_id, "graph_id": graph_id, "requested_by": requested_by},
            source="execution_graph_manager",
        )

        try:
            if graph_id == "repairable_file_patch_graph":
                result = await self._run_repairable_file_patch_graph(payload, requested_by)
            elif graph_id == "mission_safe_resume_graph":
                result = await self._run_mission_safe_resume_graph(payload, requested_by)
            else:
                raise ValueError(f"Unknown graph template: {graph_id}")

            record = {
                "run_id": run_id,
                "graph_id": graph_id,
                "requested_by": requested_by,
                "status": "completed" if result.get("ok", False) else "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": payload,
                "result": result,
            }
            self._append_run(record)

            self.event_bus.publish(
                "graph_execution_completed",
                payload={"run_id": run_id, "graph_id": graph_id, "ok": result.get("ok", False)},
                source="execution_graph_manager",
            )
            return record
        except Exception as exc:
            record = {
                "run_id": run_id,
                "graph_id": graph_id,
                "requested_by": requested_by,
                "status": "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "payload": payload,
                "error": str(exc),
            }
            self._append_run(record)

            self.event_bus.publish(
                "graph_execution_failed",
                payload={"run_id": run_id, "graph_id": graph_id, "error": str(exc)},
                severity="warning",
                source="execution_graph_manager",
            )
            return record

    async def _run_repairable_file_patch_graph(self, payload: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
        path = payload["path"]
        search = payload["search"]
        replace = payload["replace"]

        read_before = await self.tool_executor.execute(
            tool_id="file_read",
            payload={"path": path},
            requested_by=requested_by,
        )

        patch_workflow = await self.workflow_manager.execute_workflow(
            workflow_id="file_patch_verify",
            payload={"path": path, "search": search, "replace": replace},
            requested_by=requested_by,
        )

        return {
            "ok": patch_workflow.get("status") == "completed" and ((patch_workflow.get("result") or {}).get("ok") is True),
            "read_before": read_before,
            "patch_workflow": patch_workflow,
        }

    async def _run_mission_safe_resume_graph(self, payload: Dict[str, Any], requested_by: str) -> Dict[str, Any]:
        mission_id = payload["mission_id"]

        inspect = await self.tool_executor.execute(
            tool_id="process_inspect_port",
            payload={"port": 8015},
            requested_by=requested_by,
        )

        workflow = await self.workflow_manager.execute_workflow(
            workflow_id="mission_safe_continue",
            payload={"mission_id": mission_id},
            requested_by=requested_by,
        )

        ok = inspect.get("status") == "completed" and workflow.get("status") == "completed" and ((workflow.get("result") or {}).get("ok") is True)
        return {
            "ok": ok,
            "inspect": inspect,
            "workflow": workflow,
        }
