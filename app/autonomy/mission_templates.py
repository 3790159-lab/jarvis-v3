from __future__ import annotations
from typing import Dict, List


class MissionTemplateManager:
    def list_templates(self) -> List[Dict]:
        return [
            {
                "template_id": "json_config_roundtrip",
                "description": "Write a JSON config file and verify by reading it back.",
                "required_inputs": ["path", "data"],
            },
            {
                "template_id": "file_patch_and_verify",
                "description": "Patch a text file through workflow verification.",
                "required_inputs": ["path", "search", "replace"],
            },
            {
                "template_id": "service_health_repair",
                "description": "Inspect and stabilize local service port.",
                "required_inputs": ["port"],
            },
            {
                "template_id": "mission_safe_continue_template",
                "description": "Run safe mission continuation through workflow path.",
                "required_inputs": ["mission_id"],
            },
            {
                "template_id": "api_health_restart_verify",
                "description": "Inspect API port, optionally repair, then verify API health.",
                "required_inputs": ["port"],
            },
            {
                "template_id": "file_change_test_verify",
                "description": "Patch file, then run tests.",
                "required_inputs": ["path", "search", "replace"],
            },
        ]

    def build_template(self, template_id: str, payload: Dict) -> Dict:
        if template_id == "json_config_roundtrip":
            return {
                "objective": f"Write and verify JSON config at {payload['path']}",
                "steps": [
                    {
                        "type": "workflow",
                        "action": "json_write_verify",
                        "payload": {"path": payload["path"], "data": payload["data"]},
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "max_attempts": 2,
                        "max_repairs": 0,
                        "on_failure": "fail",
                    }
                ],
            }

        if template_id == "file_patch_and_verify":
            return {
                "objective": f"Patch file {payload['path']} and verify result",
                "steps": [
                    {
                        "type": "workflow",
                        "action": "file_patch_verify",
                        "payload": {
                            "path": payload["path"],
                            "search": payload["search"],
                            "replace": payload["replace"],
                        },
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "repair": "cleanup_and_unpause",
                        "repair_payload": {"mission_id": payload.get("mission_id", "mission_custom_001")},
                        "retry_after_repair": False,
                        "max_attempts": 2,
                        "max_repairs": 1,
                        "on_failure": "fail",
                    }
                ],
            }

        if template_id == "service_health_repair":
            port = int(payload["port"])
            return {
                "objective": f"Inspect and stabilize local service on port {port}",
                "steps": [
                    {
                        "type": "workflow",
                        "action": "inspect_repair_retest_port",
                        "payload": {"port": port, "allow_kill": bool(payload.get("allow_kill", False))},
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "max_attempts": 2,
                        "max_repairs": 0,
                        "on_failure": "needs_operator",
                    }
                ],
            }

        if template_id == "mission_safe_continue_template":
            mission_id = payload["mission_id"]
            return {
                "objective": f"Safely continue mission {mission_id}",
                "steps": [
                    {
                        "type": "workflow",
                        "action": "mission_safe_continue",
                        "payload": {"mission_id": mission_id},
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "repair": "cleanup_and_unpause",
                        "repair_payload": {"mission_id": mission_id},
                        "retry_after_repair": True,
                        "max_attempts": 2,
                        "max_repairs": 1,
                        "on_failure": "blocked",
                    }
                ],
            }

        if template_id == "api_health_restart_verify":
            port = int(payload["port"])
            return {
                "objective": f"Inspect and recover API on port {port}",
                "steps": [
                    {
                        "type": "workflow",
                        "action": "inspect_repair_retest_port",
                        "payload": {"port": port, "allow_kill": bool(payload.get("allow_kill", False))},
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "max_attempts": 2,
                        "max_repairs": 0,
                        "on_failure": "needs_operator",
                    },
                    {
                        "type": "tool",
                        "action": "http_get",
                        "payload": {"url": payload.get("health_url", "http://127.0.0.1:8015/api/autonomy/health")},
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "max_attempts": 2,
                        "max_repairs": 0,
                        "on_failure": "needs_operator",
                    }
                ],
            }

        if template_id == "file_change_test_verify":
            return {
                "objective": f"Patch file {payload['path']} and run tests",
                "steps": [
                    {
                        "type": "workflow",
                        "action": "file_patch_verify",
                        "payload": {
                            "path": payload["path"],
                            "search": payload["search"],
                            "replace": payload["replace"],
                        },
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "max_attempts": 2,
                        "max_repairs": 1,
                        "repair": "cleanup_and_unpause",
                        "repair_payload": {"mission_id": payload.get("mission_id", "mission_custom_001")},
                        "on_failure": "fail",
                    },
                    {
                        "type": "tool",
                        "action": "python_test",
                        "payload": {"args": ["-q"], "timeout_seconds": 600},
                        "verify": {"type": "status_in", "values": ["completed"]},
                        "max_attempts": 1,
                        "max_repairs": 0,
                        "on_failure": "needs_operator",
                    }
                ],
            }

        raise ValueError(f"Unknown mission template: {template_id}")
