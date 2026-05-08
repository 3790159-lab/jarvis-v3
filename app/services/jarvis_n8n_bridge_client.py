from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import requests


class JarvisN8NBridgeClient:
    def __init__(
        self,
        bridge_base_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> None:
        self.bridge_base_url = (bridge_base_url or os.getenv("JARVIS_N8N_BRIDGE_BASE_URL") or "http://127.0.0.1:8030").rstrip("/")
        self.timeout_seconds = int(timeout_seconds or os.getenv("JARVIS_N8N_BRIDGE_TIMEOUT_SECONDS") or "60")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        url = f"{self.bridge_base_url}{path}"
        response = requests.request(
            method=method.upper(),
            url=url,
            json=json_body,
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )

        try:
            body: Any = response.json()
        except Exception:
            body = response.text

        result = {
            "url": url,
            "status_code": response.status_code,
            "ok": response.status_code in expected_statuses,
            "body": body,
        }

        if response.status_code not in expected_statuses:
            raise RuntimeError(f"Bridge request failed: {result}")

        return result

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health", expected_statuses=(200,))

    def config(self) -> Dict[str, Any]:
        return self._request("GET", "/api/config", expected_statuses=(200,))

    def public_api_check(self) -> Dict[str, Any]:
        return self._request("GET", "/api/n8n/public-api-check", expected_statuses=(200,))

    def workflows(self, limit: int = 20) -> Dict[str, Any]:
        return self._request("GET", f"/api/n8n/workflows?limit={limit}", expected_statuses=(200,))

    def create_webhook_workflow(self, name: Optional[str] = None, webhook_path: Optional[str] = None) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/n8n/workflows/create-webhook",
            json_body={
                "name": name,
                "webhook_path": webhook_path,
            },
            expected_statuses=(200, 201),
        )

    def activate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/n8n/workflows/activate/{workflow_id}",
            expected_statuses=(200, 201, 204),
        )

    def probe_webhook(self, webhook_path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/n8n/probe/{webhook_path.strip('/')}",
            json_body={"payload": payload or {}},
            expected_statuses=(200,),
        )

    def smoke_local_webhook(self, *, name: Optional[str] = None, webhook_path: Optional[str] = None) -> Dict[str, Any]:
        stamp = str(int(time.time()))
        workflow_name = name or f"jarvis-template-smoke-{stamp}"
        path = (webhook_path or f"jarvis-template-smoke-{stamp}").strip("/")

        created = self.create_webhook_workflow(name=workflow_name, webhook_path=path)
        body = created.get("body", {})
        workflow_id = body.get("workflow_id")

        if not workflow_id:
            raise RuntimeError(f"workflow_id not found in create response: {created}")

        activated = self.activate_workflow(str(workflow_id))
        time.sleep(4)
        probe = self.probe_webhook(
            path,
            payload={
                "source": "jarvis_local_n8n_template_pack_v4",
                "message": "hello from template pack v4",
                "workflow_name": workflow_name,
            },
        )

        return {
            "create": created,
            "activate": activated,
            "probe": probe,
            "workflow_id": workflow_id,
            "webhook_path": path,
            "workflow_name": workflow_name,
        }


_bridge_singleton: Optional[JarvisN8NBridgeClient] = None


def get_jarvis_n8n_bridge_client() -> JarvisN8NBridgeClient:
    global _bridge_singleton
    if _bridge_singleton is None:
        _bridge_singleton = JarvisN8NBridgeClient()
    return _bridge_singleton