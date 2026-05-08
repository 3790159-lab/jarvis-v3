from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.services.n8n_workflow_materializer_v2 import get_n8n_workflow_materializer_v2


class N8NActionRouterMaterializerV2:
    def __init__(self) -> None:
        self.materializer = get_n8n_workflow_materializer_v2()
        self.module_file = str(Path(__file__).resolve())

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "n8n_action_router_materializer_v2",
            "module_file": self.module_file,
            "n8n": self.materializer.health(),
            "actions": [
                "create_only:workflow",
                "publish:workflow",
                "publish_and_probe:workflow",
                "debug:state",
                "debug:dry_run_payload",
            ],
        }

    def debug_state(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "n8n_action_router_materializer_v2",
            "module_file": self.module_file,
            "materializer_debug": self.materializer.debug_snapshot(),
        }

    def dry_run_payload(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        return {
            "status": "ok",
            "action": "debug:dry_run_payload",
            "result": self.materializer.debug_snapshot(
                workflow_name=payload.get("name"),
                webhook_path=payload.get("webhook_path"),
            ),
        }

    def handle(self, action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}

        if action == "debug:state":
            return self.debug_state()

        if action == "debug:dry_run_payload":
            return self.dry_run_payload(payload)

        if action == "create_only:workflow":
            result = self.materializer.create_workflow(
                name=payload.get("name"),
                webhook_path=payload.get("webhook_path"),
            )
            return {"status": "ok", "action": action, "result": result}

        if action == "publish:workflow":
            result = self.materializer.publish_workflow(
                name=payload.get("name"),
                webhook_path=payload.get("webhook_path"),
            )
            return {"status": "ok", "action": action, "result": result}

        if action == "publish_and_probe:workflow":
            publish_result = self.materializer.publish_workflow(
                name=payload.get("name"),
                webhook_path=payload.get("webhook_path"),
            )
            probe_result = self.materializer.probe_webhook(
                webhook_url=publish_result["webhook_url"],
                payload=payload.get("probe_payload"),
            )
            return {
                "status": "ok",
                "action": action,
                "result": {
                    "publish": publish_result,
                    "probe": probe_result,
                },
            }

        raise ValueError(f"Unsupported action: {action}")