from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV_PATH = PROJECT_ROOT / ".env"
APP_FILE = str(Path(__file__).resolve())

app = FastAPI(title="Jarvis N8N Stage B Sidecar", version="1.1.0")


class PublishWorkflowRequest(BaseModel):
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    response_text: Optional[str] = None
    probe_payload: Optional[Dict[str, Any]] = None


def load_dotenv_loose(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data

    raw = path.read_text(encoding="utf-8", errors="ignore")
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data


class N8NStageBSidecar:
    def __init__(self) -> None:
        self.env_file = load_dotenv_loose(ENV_PATH)

        self.base_url = (
            os.getenv("N8N_BASE_URL")
            or self.env_file.get("N8N_BASE_URL")
            or "https://daniliyc.app.n8n.cloud"
        ).rstrip("/")

        self.webhook_base_url = (
            os.getenv("N8N_WEBHOOK_BASE_URL")
            or self.env_file.get("N8N_WEBHOOK_BASE_URL")
            or self.base_url
        ).rstrip("/")

        self.api_key = (
            os.getenv("N8N_API_KEY")
            or self.env_file.get("N8N_API_KEY")
            or os.getenv("N8N_BEARER_TOKEN")
            or self.env_file.get("N8N_BEARER_TOKEN")
            or ""
        )

        self.timeout_seconds = int(
            os.getenv("N8N_TIMEOUT_SECONDS")
            or self.env_file.get("N8N_TIMEOUT_SECONDS")
            or "45"
        )

        self.module_file = APP_FILE

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        expected_statuses: tuple[int, ...] = (200, 201),
    ) -> Any:
        url = f"{self.base_url}{path}"
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=self._headers(),
            json=json_body,
            params=params,
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )

        if response.status_code not in expected_statuses:
            raise RuntimeError(
                f"n8n API request failed: {method.upper()} {url} -> "
                f"{response.status_code}: {response.text}"
            )

        if not response.content:
            return {}

        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type.lower():
            return response.json()

        return {"text": response.text}

    @staticmethod
    def _extract_workflow_id(payload: Any) -> Optional[str]:
        if isinstance(payload, dict):
            for key in ("id", "workflowId"):
                if payload.get(key) is not None:
                    return str(payload[key])

            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("id", "workflowId"):
                    if data.get(key) is not None:
                        return str(data[key])

            items = payload.get("items")
            if isinstance(items, list) and items:
                first = items[0]
                if isinstance(first, dict):
                    for key in ("id", "workflowId"):
                        if first.get(key) is not None:
                            return str(first[key])
        return None

    def build_known_good_payload(self, workflow_name: str, webhook_path: str) -> Dict[str, Any]:
        return {
            "name": workflow_name,
            "nodes": [
                {
                    "name": "Jarvis Webhook",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 1,
                    "position": [600, 300],
                    "parameters": {
                        "httpMethod": "POST",
                        "path": webhook_path,
                        "options": {},
                    },
                }
            ],
            "connections": {},
            "settings": {},
        }

    def config_snapshot(self) -> Dict[str, Any]:
        return {
            "service": "jarvis_n8n_stage_b_sidecar",
            "module_file": self.module_file,
            "env_path": str(ENV_PATH),
            "env_path_exists": ENV_PATH.exists(),
            "base_url": self.base_url,
            "webhook_base_url": self.webhook_base_url,
            "has_api_key": bool(self.api_key),
            "timeout_seconds": self.timeout_seconds,
            "create_payload_mode": "B1_v1_with_settings_empty",
            "env_sources": {
                "N8N_BASE_URL": "os.environ" if os.getenv("N8N_BASE_URL") else ("dotenv" if self.env_file.get("N8N_BASE_URL") else "default"),
                "N8N_WEBHOOK_BASE_URL": "os.environ" if os.getenv("N8N_WEBHOOK_BASE_URL") else ("dotenv" if self.env_file.get("N8N_WEBHOOK_BASE_URL") else "derived"),
                "N8N_API_KEY": "os.environ" if os.getenv("N8N_API_KEY") else ("dotenv" if self.env_file.get("N8N_API_KEY") else "missing"),
                "N8N_TIMEOUT_SECONDS": "os.environ" if os.getenv("N8N_TIMEOUT_SECONDS") else ("dotenv" if self.env_file.get("N8N_TIMEOUT_SECONDS") else "default"),
            },
        }

    def health(self) -> Dict[str, Any]:
        result = {
            "status": "ok",
            **self.config_snapshot(),
        }
        try:
            self._request("GET", "/api/v1/workflows", params={"limit": 1}, expected_statuses=(200,))
            result["api_status"] = "reachable"
        except Exception as exc:
            result["api_status"] = "unreachable"
            result["api_error"] = str(exc)
        return result

    def debug_snapshot(self, workflow_name: Optional[str] = None, webhook_path: Optional[str] = None) -> Dict[str, Any]:
        suffix = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        workflow_name = workflow_name or f"jarvis-sidecar-{suffix}"
        webhook_path = (webhook_path or f"jarvis-sidecar-{suffix}").strip("/")
        payload = self.build_known_good_payload(workflow_name, webhook_path)
        return {
            **self.config_snapshot(),
            "workflow_name": workflow_name,
            "webhook_path": webhook_path,
            "payload_keys": list(payload.keys()),
            "payload": payload,
        }

    def create_workflow(self, *, name: Optional[str] = None, webhook_path: Optional[str] = None) -> Dict[str, Any]:
        suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        workflow_name = name or f"jarvis-sidecar-{suffix}"
        safe_webhook_path = (webhook_path or f"jarvis-sidecar-{suffix}").strip("/")
        payload = self.build_known_good_payload(workflow_name, safe_webhook_path)

        try:
            created = self._request(
                "POST",
                "/api/v1/workflows",
                json_body=payload,
                expected_statuses=(200, 201),
            )
        except Exception as exc:
            raise RuntimeError(
                f"{exc} | create_payload_mode=B1_v1_with_settings_empty | payload={payload}"
            )

        workflow_id = self._extract_workflow_id(created)
        if not workflow_id:
            raise RuntimeError(f"Unable to extract workflow id from n8n response: {created}")

        return {
            "ok": True,
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "webhook_path": safe_webhook_path,
            "webhook_url": f"{self.webhook_base_url}/webhook/{safe_webhook_path}",
            "create_payload_mode": "B1_v1_with_settings_empty",
            "used_payload": payload,
            "create_response": created,
        }


sidecar = N8NStageBSidecar()


@app.get("/health")
def health() -> Dict[str, Any]:
    return sidecar.health()


@app.get("/config")
def config() -> Dict[str, Any]:
    return sidecar.config_snapshot()


@app.get("/debug-state")
def debug_state() -> Dict[str, Any]:
    return sidecar.debug_snapshot()


@app.post("/dry-run-payload")
def dry_run_payload(request: PublishWorkflowRequest) -> Dict[str, Any]:
    return sidecar.debug_snapshot(
        workflow_name=request.name,
        webhook_path=request.webhook_path,
    )


@app.post("/create-only")
def create_only(request: PublishWorkflowRequest) -> Dict[str, Any]:
    try:
        return sidecar.create_workflow(
            name=request.name,
            webhook_path=request.webhook_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/routes")
def routes() -> Dict[str, Any]:
    return {
        "paths": sorted(
            {
                getattr(route, "path", None)
                for route in app.routes
                if getattr(route, "path", None)
            }
        )
    }