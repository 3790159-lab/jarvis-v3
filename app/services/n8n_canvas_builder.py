from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

if load_dotenv is not None:
    try:
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


@dataclass(frozen=True)
class N8nCanvasBuilderConfig:
    artifact_dir: Path
    max_steps: int


class N8nCanvasBuilder:
    def __init__(self, config: Optional[N8nCanvasBuilderConfig] = None) -> None:
        self.config = config or self.from_env()

    @staticmethod
    def from_env() -> N8nCanvasBuilderConfig:
        raw_dir = (os.getenv("JARVIS_CANVAS_ARTIFACT_DIR", "jarvis_stage3_artifacts/n8n_supervisor/canvas_exports") or "").strip()
        artifact_dir = Path(raw_dir)
        if not artifact_dir.is_absolute():
            artifact_dir = _PROJECT_ROOT / artifact_dir

        return N8nCanvasBuilderConfig(
            artifact_dir=artifact_dir,
            max_steps=max(1, _env_int("JARVIS_CANVAS_MAX_STEPS", 24)),
        )

    def _ensure_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        self._ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_text(self, path: Path, content: str) -> None:
        self._ensure_dir(path.parent)
        path.write_text(content, encoding="utf-8")

    def _normalize_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(steps, list) or not steps:
            raise RuntimeError("Canvas builder expects a non-empty list of steps")

        if len(steps) > self.config.max_steps:
            raise RuntimeError(f"Too many steps: {len(steps)} > {self.config.max_steps}")

        normalized: List[Dict[str, Any]] = []
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise RuntimeError(f"Step #{idx} must be an object")

            action = str(step.get("action", "") or "").strip()
            if not action:
                raise RuntimeError(f"Step #{idx} is missing action")

            normalized.append(
                {
                    "index": idx,
                    "step_id": str(step.get("step_id", f"step-{idx}")),
                    "action": action,
                    "intent": str(step.get("intent", action)),
                    "payload": step.get("payload", {}),
                }
            )
        return normalized

    def _new_node_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def _manual_trigger_node(self) -> Dict[str, Any]:
        return {
            "parameters": {},
            "id": self._new_node_id(),
            "name": "Manual Trigger",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [260, 300],
        }

    def _webhook_node(self, webhook_path: str) -> Dict[str, Any]:
        clean_path = webhook_path.strip().strip("/")
        return {
            "parameters": {
                "httpMethod": "POST",
                "path": clean_path,
                "responseMode": "onReceived",
                "options": {},
            },
            "id": self._new_node_id(),
            "name": "Jarvis Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [260, 520],
        }

    def _set_node(self, step: Dict[str, Any], x: int, y: int) -> Dict[str, Any]:
        payload_string = json.dumps(step["payload"], ensure_ascii=False)
        return {
            "parameters": {
                "assignments": {
                    "assignments": [
                        {
                            "id": self._new_node_id(),
                            "name": "pipeline_step_id",
                            "value": step["step_id"],
                            "type": "string",
                        },
                        {
                            "id": self._new_node_id(),
                            "name": "pipeline_action",
                            "value": step["action"],
                            "type": "string",
                        },
                        {
                            "id": self._new_node_id(),
                            "name": "pipeline_intent",
                            "value": step["intent"],
                            "type": "string",
                        },
                        {
                            "id": self._new_node_id(),
                            "name": "pipeline_order",
                            "value": step["index"],
                            "type": "number",
                        },
                        {
                            "id": self._new_node_id(),
                            "name": "pipeline_payload_json",
                            "value": payload_string,
                            "type": "string",
                        },
                    ]
                },
                "options": {},
            },
            "id": self._new_node_id(),
            "name": f"Step {step['index']} - {step['action']}",
            "type": "n8n-nodes-base.set",
            "typeVersion": 3.4,
            "position": [x, y],
        }

    def _build_connections(
        self,
        manual_name: str,
        webhook_name: str,
        step_nodes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        connections: Dict[str, Any] = {}

        if not step_nodes:
            return connections

        first_step_name = step_nodes[0]["name"]
        connections[manual_name] = {
            "main": [[{"node": first_step_name, "type": "main", "index": 0}]]
        }
        connections[webhook_name] = {
            "main": [[{"node": first_step_name, "type": "main", "index": 0}]]
        }

        for current, nxt in zip(step_nodes, step_nodes[1:]):
            connections[current["name"]] = {
                "main": [[{"node": nxt["name"], "type": "main", "index": 0}]]
            }

        return connections

    def build(
        self,
        *,
        workflow_name: str,
        webhook_path: str,
        steps: List[Dict[str, Any]],
        description: str = "",
    ) -> Dict[str, Any]:
        normalized_steps = self._normalize_steps(steps)
        build_id = f"canvas-{uuid.uuid4().hex[:12]}"
        build_dir = self._ensure_dir(self.config.artifact_dir / build_id)

        manual = self._manual_trigger_node()
        webhook = self._webhook_node(webhook_path)

        step_nodes: List[Dict[str, Any]] = []
        x = 560
        y = 410
        for step in normalized_steps:
            step_nodes.append(self._set_node(step, x, y))
            x += 320

        workflow_json = {
            "name": workflow_name,
            "nodes": [manual, webhook, *step_nodes],
            "connections": self._build_connections(manual["name"], webhook["name"], step_nodes),
            "pinData": {},
            "settings": {
                "executionOrder": "v1"
            },
            "staticData": None,
            "meta": {
                "generatedBy": "jarvis_n8n_canvas_builder",
                "buildId": build_id,
                "description": description,
            },
            "versionId": str(uuid.uuid4()),
            "active": False,
            "tags": [],
        }

        manifest = {
            "build_id": build_id,
            "created_at": _utc_now(),
            "workflow_name": workflow_name,
            "webhook_path": webhook_path,
            "description": description,
            "step_count": len(normalized_steps),
            "steps": normalized_steps,
            "artifact_dir": str(build_dir),
            "workflow_json_path": str(build_dir / "n8n_workflow_stub.json"),
            "manifest_path": str(build_dir / "pipeline_manifest.json"),
            "import_notes_path": str(build_dir / "import_notes.md"),
        }

        import_notes = f"""# Jarvis n8n Canvas Build

Build ID: {build_id}

Workflow name: {workflow_name}
Webhook path: {webhook_path}

## What this artifact is
This is an editable n8n scaffold generated by Jarvis.
It contains:
- Manual Trigger
- Jarvis Webhook
- one Set node per pipeline step

## Expected use
1. Open n8n
2. Create a new workflow
3. Import `n8n_workflow_stub.json`
4. Inspect the generated step nodes
5. Replace placeholder Set nodes with real logic
6. Save and publish when ready

## Steps
{json.dumps(normalized_steps, ensure_ascii=False, indent=2)}
"""

        self._write_json(build_dir / "pipeline_manifest.json", manifest)
        self._write_json(build_dir / "n8n_workflow_stub.json", workflow_json)
        self._write_text(build_dir / "import_notes.md", import_notes)

        return {
            "status": "ok",
            "build_id": build_id,
            "workflow_name": workflow_name,
            "webhook_path": webhook_path,
            "step_count": len(normalized_steps),
            "artifact_dir": str(build_dir),
            "workflow_json_path": str(build_dir / "n8n_workflow_stub.json"),
            "manifest_path": str(build_dir / "pipeline_manifest.json"),
            "import_notes_path": str(build_dir / "import_notes.md"),
            "steps": normalized_steps,
        }


_BUILDER: Optional[N8nCanvasBuilder] = None


def get_n8n_canvas_builder() -> N8nCanvasBuilder:
    global _BUILDER
    if _BUILDER is None:
        _BUILDER = N8nCanvasBuilder()
    return _BUILDER