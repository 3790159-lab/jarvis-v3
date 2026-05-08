from __future__ import annotations

from typing import Any, Dict, List


class ChainManager:
    def __init__(self, tool_executor: Any, event_bus: Any) -> None:
        self.tool_executor = tool_executor
        self.event_bus = event_bus

    def list_templates(self) -> List[Dict[str, Any]]:
        return [
            {
                "chain_id": "write_append_read",
                "description": "Write text, append text, then read file back.",
                "required_inputs": ["path", "initial_content", "append_content"],
            },
            {
                "chain_id": "json_write_read",
                "description": "Write JSON then read it back.",
                "required_inputs": ["path", "data"],
            },
            {
                "chain_id": "inspect_and_test",
                "description": "Inspect API port and run pytest.",
                "required_inputs": ["port"],
            },
        ]

    async def execute_template(self, chain_id: str, payload: Dict[str, Any], requested_by: str = "chain_api") -> Dict[str, Any]:
        if chain_id == "write_append_read":
            steps = [
                {"tool_id": "file_write", "payload": {"path": payload["path"], "content": payload["initial_content"]}},
                {"tool_id": "file_append", "payload": {"path": payload["path"], "content": payload["append_content"]}},
                {"tool_id": "file_read", "payload": {"path": payload["path"]}},
            ]
        elif chain_id == "json_write_read":
            steps = [
                {"tool_id": "json_write", "payload": {"path": payload["path"], "data": payload["data"]}},
                {"tool_id": "json_read", "payload": {"path": payload["path"]}},
            ]
        elif chain_id == "inspect_and_test":
            steps = [
                {"tool_id": "process_inspect_port", "payload": {"port": int(payload["port"])}},
                {"tool_id": "python_test", "payload": {"args": ["-q"], "timeout_seconds": 600}},
            ]
        else:
            raise ValueError(f"Unknown chain template: {chain_id}")

        self.event_bus.publish(
            "chain_execution_started",
            payload={"chain_id": chain_id, "requested_by": requested_by},
            source="chain_manager",
        )

        result = await self.tool_executor.execute_plan(steps=steps, requested_by=requested_by)

        self.event_bus.publish(
            "chain_execution_completed",
            payload={"chain_id": chain_id, "ok": result.get("ok", False)},
            source="chain_manager",
        )
        return result
