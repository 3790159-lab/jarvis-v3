from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

if load_dotenv is not None:
    try:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


def _normalize_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _normalize_path(path: str, fallback: str) -> str:
    value = (path or "").strip()
    if not value:
        value = fallback
    if not value.startswith("/"):
        value = "/" + value
    return value


def _json_or_text(text: str) -> Any:
    text = text or ""
    try:
        return json.loads(text)
    except Exception:
        return text


@dataclass(frozen=True)
class N8nBridgeConfig:
    enabled: bool
    base_url: str
    health_url: str
    trigger_path: str
    test_trigger_path: str
    timeout_seconds: int
    shared_key: str


class N8nBridge:
    def __init__(self, config: Optional[N8nBridgeConfig] = None) -> None:
        self.config = config or self.from_env()

    @staticmethod
    def from_env() -> N8nBridgeConfig:
        base_url = _normalize_base(os.getenv("JARVIS_N8N_BASE_URL", "http://127.0.0.1:5678"))
        health_url = (os.getenv("JARVIS_N8N_HEALTH_URL", "") or "").strip()
        if not health_url and base_url:
            health_url = f"{base_url}/healthz"

        return N8nBridgeConfig(
            enabled=_env_bool("JARVIS_N8N_ENABLED", False),
            base_url=base_url,
            health_url=health_url,
            trigger_path=_normalize_path(
                os.getenv("JARVIS_N8N_TRIGGER_PATH", "/webhook/jarvis/inbox_v2"),
                "/webhook/jarvis/inbox_v2",
            ),
            test_trigger_path=_normalize_path(
                os.getenv("JARVIS_N8N_TEST_TRIGGER_PATH", "/webhook-test/jarvis/inbox_v2"),
                "/webhook-test/jarvis/inbox_v2",
            ),
            timeout_seconds=_env_int("JARVIS_N8N_TIMEOUT_SECONDS", 180),
            shared_key=(os.getenv("JARVIS_N8N_SHARED_KEY", "") or "").strip(),
        )

    def _headers(self, source: str, mission_id: str, task_id: str) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Jarvis-Source": source,
            "X-Jarvis-Mission-Id": mission_id,
            "X-Jarvis-Task-Id": task_id,
        }
        if self.config.shared_key:
            headers["X-Jarvis-Key"] = self.config.shared_key
        return headers

    def _request(
        self,
        method: str,
        url: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "application/json")

        data = None
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = urllib_request.Request(
            url=url,
            data=data,
            headers=request_headers,
            method=method.upper(),
        )

        try:
            with urllib_request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
                text = raw.decode("utf-8", errors="replace")
                return {
                    "ok": True,
                    "status_code": int(response.getcode()),
                    "headers": dict(response.headers.items()),
                    "data": _json_or_text(text),
                    "text": text,
                }
        except urllib_error.HTTPError as exc:
            raw = exc.read()
            text = raw.decode("utf-8", errors="replace")
            return {
                "ok": False,
                "status_code": int(exc.code),
                "headers": dict(exc.headers.items()) if exc.headers else {},
                "data": _json_or_text(text),
                "text": text,
            }
        except urllib_error.URLError as exc:
            raise RuntimeError(f"Failed to reach n8n at {url}: {exc.reason}") from exc

    def health(self) -> Dict[str, Any]:
        cfg = self.config
        result: Dict[str, Any] = {
            "enabled": cfg.enabled,
            "base_url": cfg.base_url,
            "health_url": cfg.health_url,
            "trigger_path": cfg.trigger_path,
            "test_trigger_path": cfg.test_trigger_path,
            "timeout_seconds": cfg.timeout_seconds,
            "has_shared_key": bool(cfg.shared_key),
        }

        if not cfg.enabled:
            result["reachable"] = False
            result["note"] = "JARVIS_N8N_ENABLED is false"
            return result

        if not cfg.health_url:
            result["reachable"] = False
            result["note"] = "health url missing"
            return result

        try:
            probe = self._request("GET", cfg.health_url, body=None, headers={"Accept": "application/json"})
            result["reachable"] = probe["ok"]
            result["status_code"] = probe["status_code"]
            result["response"] = probe["data"]
            return result
        except Exception as exc:
            result["reachable"] = False
            result["error"] = str(exc)
            return result

    def dispatch(
        self,
        *,
        action: str = "echo",
        intent: str = "automation",
        payload: Optional[Any] = None,
        mission_id: Optional[str] = None,
        task_id: Optional[str] = None,
        source: str = "jarvis",
        use_test_webhook: bool = False,
    ) -> Dict[str, Any]:
        cfg = self.config

        if not cfg.enabled:
            raise RuntimeError("JARVIS_N8N_ENABLED is false")

        if not cfg.base_url:
            raise RuntimeError("JARVIS_N8N_BASE_URL is empty")

        path = cfg.test_trigger_path if use_test_webhook else cfg.trigger_path
        url = f"{cfg.base_url}{path}"

        mission_id = mission_id or f"n8n-{uuid.uuid4().hex[:12]}"
        task_id = task_id or "dispatch-1"

        body: Dict[str, Any] = {
            "source": source,
            "mission_id": mission_id,
            "task_id": task_id,
            "intent": intent,
            "action": action,
            "payload": payload if payload is not None else {},
        }

        result = self._request(
            "POST",
            url,
            body=body,
            headers=self._headers(source=source, mission_id=mission_id, task_id=task_id),
        )
        result["url"] = url
        result["mission_id"] = mission_id
        result["task_id"] = task_id
        result["used_test_webhook"] = use_test_webhook
        return result


_BRIDGE: Optional[N8nBridge] = None


def get_n8n_bridge() -> N8nBridge:
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = N8nBridge()
    return _BRIDGE