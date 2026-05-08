from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .auth_manager import AuthManager
from .connector_registry import ConnectorRegistry


class ConnectorExecutionResult(BaseModel):
    ok: bool
    connector_id: str
    service: str
    action: str
    dry_run: bool = False
    authorized: bool = False
    configured: bool = False
    message: str = ""
    output: dict[str, Any] = Field(default_factory=dict)


class ConnectorExecutor:
    def __init__(
        self,
        connector_registry: ConnectorRegistry,
        auth_manager: AuthManager,
        log_path: Path,
    ) -> None:
        self.connector_registry = connector_registry
        self.auth_manager = auth_manager
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, event: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _authorized(self, agent_id: str, connector_id: str, capability: str) -> tuple[bool, str]:
        connector = self.connector_registry.get(connector_id)
        if not connector:
            return False, f"Connector not found: {connector_id}"

        if connector.allowed_agents and agent_id not in connector.allowed_agents:
            return False, f"Agent '{agent_id}' is not allowed to use connector '{connector_id}'"

        if connector.allowed_capabilities and capability not in connector.allowed_capabilities:
            return False, f"Capability '{capability}' is not allowed for connector '{connector_id}'"

        return True, "authorized"

    def _http_json_get(self, url: str, timeout: float = 5.0) -> dict[str, Any]:
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(data)
            except Exception:
                return {"raw": data}

    def _http_json_post(self, url: str, body: dict[str, Any], headers: dict[str, str] | None = None, timeout: float = 8.0) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(payload)
            except Exception:
                return {"raw": payload}

    def execute(
        self,
        agent_id: str,
        connector_id: str,
        capability: str,
        action: str,
        payload: dict[str, Any] | None = None,
        dry_run: bool = True,
    ) -> ConnectorExecutionResult:
        payload = dict(payload or {})
        connector = self.connector_registry.get(connector_id)
        if not connector:
            return ConnectorExecutionResult(
                ok=False,
                connector_id=connector_id,
                service="unknown",
                action=action,
                dry_run=dry_run,
                authorized=False,
                configured=False,
                message=f"Connector not found: {connector_id}",
            )

        allowed, reason = self._authorized(agent_id, connector_id, capability)
        health = self.auth_manager.connector_health(connector_id)

        event = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "agent_id": agent_id,
            "connector_id": connector_id,
            "service": connector.service,
            "capability": capability,
            "action": action,
            "dry_run": dry_run,
            "authorized": allowed,
            "configured": health.get("configured", False),
        }

        if not allowed:
            event["ok"] = False
            event["message"] = reason
            self._log(event)
            return ConnectorExecutionResult(
                ok=False,
                connector_id=connector_id,
                service=connector.service,
                action=action,
                dry_run=dry_run,
                authorized=False,
                configured=health.get("configured", False),
                message=reason,
            )

        if not health.get("configured", False) and not dry_run:
            msg = f"Connector '{connector_id}' is not configured for live execution."
            event["ok"] = False
            event["message"] = msg
            self._log(event)
            return ConnectorExecutionResult(
                ok=False,
                connector_id=connector_id,
                service=connector.service,
                action=action,
                dry_run=dry_run,
                authorized=True,
                configured=False,
                message=msg,
            )

        try:
            result = self._dispatch(connector.service, connector.metadata, action, payload, dry_run)
            event["ok"] = bool(result.get("ok", False))
            event["message"] = result.get("message", "")
            self._log(event)

            return ConnectorExecutionResult(
                ok=bool(result.get("ok", False)),
                connector_id=connector_id,
                service=connector.service,
                action=action,
                dry_run=dry_run,
                authorized=True,
                configured=health.get("configured", False),
                message=result.get("message", ""),
                output=result.get("output", {}),
            )
        except Exception as exc:
            event["ok"] = False
            event["message"] = str(exc)
            self._log(event)
            return ConnectorExecutionResult(
                ok=False,
                connector_id=connector_id,
                service=connector.service,
                action=action,
                dry_run=dry_run,
                authorized=True,
                configured=health.get("configured", False),
                message=str(exc),
            )

    def _dispatch(self, service: str, metadata: dict[str, Any], action: str, payload: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        service = str(service or "").strip().lower()

        if service in {"openai", "claude_bridge", "google_workspace", "telegram"}:
            return {
                "ok": True,
                "message": f"{service} connector passed guarded action '{action}'.",
                "output": {
                    "action": action,
                    "dry_run": dry_run,
                    "mode": "guarded_binding_check",
                },
            }

        if service == "ollama":
            base_url = str(metadata.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
            if dry_run:
                return {
                    "ok": True,
                    "message": "Ollama dry-run prepared.",
                    "output": {"base_url": base_url, "action": action},
                }
            if action == "health":
                data = self._http_json_get(f"{base_url}/api/tags", timeout=5.0)
                return {
                    "ok": True,
                    "message": "Ollama health call completed.",
                    "output": data,
                }
            return {
                "ok": True,
                "message": f"Ollama action '{action}' acknowledged.",
                "output": {"base_url": base_url},
            }

        if service == "n8n":
            base_url = str(metadata.get("base_url") or "").rstrip("/")
            webhook_path = str(payload.get("webhook_path") or metadata.get("webhook_path") or "").strip("/")
            body = payload.get("body", {})
            if dry_run:
                return {
                    "ok": True,
                    "message": "n8n dry-run prepared.",
                    "output": {
                        "base_url": base_url,
                        "webhook_path": webhook_path,
                        "body_preview": body,
                    },
                }

            if action == "health":
                data = self._http_json_get(f"{base_url}/healthz", timeout=5.0)
                return {
                    "ok": True,
                    "message": "n8n health check completed.",
                    "output": data,
                }

            if action == "webhook_invoke":
                if not webhook_path:
                    return {
                        "ok": False,
                        "message": "Missing webhook_path for n8n webhook_invoke.",
                        "output": {},
                    }
                url = f"{base_url}/webhook/{webhook_path}"
                data = self._http_json_post(url, body=body, timeout=8.0)
                return {
                    "ok": True,
                    "message": "n8n webhook invocation completed.",
                    "output": data,
                }

            return {
                "ok": False,
                "message": f"Unsupported n8n action: {action}",
                "output": {},
            }

        return {
            "ok": False,
            "message": f"Unsupported connector service: {service}",
            "output": {},
        }

    def read_log(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        selected = lines[-max(1, limit):]
        out: list[dict[str, Any]] = []
        for line in selected:
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"raw": line})
        return out