from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import requests


class N8NWorkflowMaterializer:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        webhook_base_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("N8N_BASE_URL") or "http://127.0.0.1:5678").rstrip("/")
        self.api_key = api_key or os.getenv("N8N_API_KEY") or os.getenv("N8N_BEARER_TOKEN") or ""
        self.webhook_base_url = (webhook_base_url or os.getenv("N8N_WEBHOOK_BASE_URL") or self.base_url).rstrip("/")
        self.timeout_seconds = int(timeout_seconds or os.getenv("N8N_TIMEOUT_SECONDS") or "45")
        self.module_file = str(Path(__file__).resolve())

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
        # EXACT candidate that succeeded in direct truth probe:
        # B1_v1_with_settings_empty
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

    def debug_snapshot(self, workflow_name: Optional[str] = None, webhook_path: Optional[str] = None) -> Dict[str, Any]:
        suffix = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        workflow_name = workflow_name or f"jarvis-debug-{suffix}"
        webhook_path = (webhook_path or f"jarvis-debug-{suffix}").strip("/")
        payload = self.build_known_good_payload(workflow_name, webhook_path)
        return {
            "module_file": self.module_file,
            "create_payload_mode": "B1_v1_with_settings_empty",
            "workflow_name": workflow_name,
            "webhook_path": webhook_path,
            "payload": payload,
            "payload_keys": list(payload.keys()),
        }

    def health(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "service": "n8n_workflow_materializer",
            "module_file": self.module_file,
            "base_url": self.base_url,
            "webhook_base_url": self.webhook_base_url,
            "has_api_key": bool(self.api_key),
            "timeout_seconds": self.timeout_seconds,
            "create_payload_mode": "B1_v1_with_settings_empty",
        }
        try:
            self._request("GET", "/api/v1/workflows", params={"limit": 1}, expected_statuses=(200,))
            result["api_status"] = "reachable"
        except Exception as exc:
            result["api_status"] = "unreachable"
            result["api_error"] = str(exc)
        return result

    def _get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/v1/workflows/{workflow_id}", expected_statuses=(200,))

    def _activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        attempts = []

        for method, path, body, statuses in [
            ("POST", f"/api/v1/workflows/{workflow_id}/activate", None, (200, 201, 204)),
            ("PATCH", f"/api/v1/workflows/{workflow_id}", {"active": True}, (200,)),
        ]:
            item: Dict[str, Any] = {"method": method, "path": path}
            try:
                item["response"] = self._request(method, path, json_body=body, expected_statuses=statuses)
                item["ok"] = True
            except Exception as exc:
                item["ok"] = False
                item["error"] = str(exc)

            try:
                item["workflow_state"] = self._get_workflow(workflow_id)
            except Exception as exc:
                item["workflow_state_error"] = str(exc)

            attempts.append(item)
            time.sleep(2)

        return {"attempts": attempts}

    def publish_workflow(
        self,
        *,
        name: Optional[str] = None,
        webhook_path: Optional[str] = None,
        response_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        workflow_name = name or f"jarvis-stable-{suffix}"
        safe_webhook_path = (webhook_path or f"jarvis-stable-{suffix}").strip("/")

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

        activate_response = self._activate_workflow(workflow_id)

        return {
            "ok": True,
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "webhook_path": safe_webhook_path,
            "webhook_url": f"{self.webhook_base_url}/webhook/{safe_webhook_path}",
            "create_payload_mode": "B1_v1_with_settings_empty",
            "used_payload": payload,
            "create_response": created,
            "activate_response": activate_response,
        }

    def probe_webhook(self, webhook_url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        probe_payload = payload or {"source": "jarvis_probe", "ts": int(time.time())}
        response = requests.post(
            webhook_url,
            json=probe_payload,
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )

        try:
            body: Any = response.json()
        except Exception:
            body = response.text

        return {
            "status_code": response.status_code,
            "ok": 200 <= response.status_code < 300,
            "headers": {k: v for k, v in response.headers.items() if k.lower() in {"content-type", "location", "server", "x-powered-by"}},
            "body": body,
        }


_materializer_singleton = None

def get_n8n_workflow_materializer():
    global _materializer_singleton
    if _materializer_singleton is None:
        _materializer_singleton = N8NWorkflowMaterializer()
    return _materializer_singleton