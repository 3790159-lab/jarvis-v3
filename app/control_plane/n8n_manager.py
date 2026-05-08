from __future__ import annotations

import json
import os
import socket
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def _read_text_lenient(path: Path) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    return data.decode("utf-8", errors="ignore")


def _load_json_lenient(path: Path, default):
    if not path.exists():
        return default
    try:
        raw = _read_text_lenient(path)
        if not raw.strip():
            return default
        return json.loads(raw)
    except Exception:
        return default


def _write_json_nobom(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


class N8NManager:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.connectors_path = self.base_dir / "connectors.json"
        self.secrets_path = self.base_dir / "secrets_registry.json"
        self.state_path = self.base_dir / "n8n_live_state.json"
        self._ensure_state()

    def _ensure_state(self) -> None:
        if not self.state_path.exists():
            _write_json_nobom(self.state_path, {
                "live_enabled": False,
                "verified": False,
                "last_verify_status": "",
                "last_verify_ts": 0.0,
                "last_webhook_status": "",
                "last_webhook_ts": 0.0,
            })

    def state(self) -> dict:
        self._ensure_state()
        return _load_json_lenient(self.state_path, {})

    def _save_state(self, state: dict) -> None:
        _write_json_nobom(self.state_path, state)

    def _connector(self) -> dict:
        raw = _load_json_lenient(self.connectors_path, {"connectors": []})
        for item in raw.get("connectors", []):
            if str(item.get("connector_id") or "") == "n8n_primary":
                return item
        for item in raw.get("connectors", []):
            if str(item.get("service") or "") == "n8n":
                return item
        return {}

    def _resolve_secret(self, secret_name: str) -> str:
        raw = _load_json_lenient(self.secrets_path, {"secrets": {}})
        item = (raw.get("secrets") or {}).get(secret_name, {})
        env_var = str(item.get("env_var") or "")
        file_path = str(item.get("file_path") or "")

        if env_var and os.getenv(env_var):
            return os.getenv(env_var, "")
        if file_path and Path(file_path).exists():
            try:
                return _read_text_lenient(Path(file_path)).strip()
            except Exception:
                return ""
        return ""

    def config(self) -> dict:
        c = self._connector()
        meta = c.get("metadata", {}) or {}
        webhook_path = str(meta.get("webhook_path") or "").strip("/") or "jarvis-live"

        return {
            "base_url": str(meta.get("base_url") or "").rstrip("/"),
            "api_key": self._resolve_secret(str(meta.get("secret_name") or "n8n_api_key")),
            "api_auth_header_name": str(meta.get("api_auth_header_name") or "X-N8N-API-KEY"),
            "webhook_auth_header_name": str(meta.get("webhook_auth_header_name") or "API-KEY"),
            "webhook_secret": self._resolve_secret(str(meta.get("webhook_secret_name") or "n8n_webhook_secret")),
            "webhook_path": webhook_path,
            "live_enabled": bool(meta.get("live_enabled", False)),
        }

    def _socket_check(self, base_url: str) -> dict:
        if not base_url:
            return {"reachable": False, "host": "", "port": 0, "error": "missing_base_url"}
        try:
            parsed = urlparse(base_url)
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with socket.create_connection((host, port), timeout=2):
                return {"reachable": True, "host": host, "port": port, "error": ""}
        except Exception as exc:
            return {"reachable": False, "host": parsed.hostname if 'parsed' in locals() else "", "port": parsed.port if 'parsed' in locals() and parsed.port else 0, "error": str(exc)[:200]}

    def _http_ping(self, base_url: str) -> dict:
        if not base_url:
            return {"ok": False, "status": 0, "error": "missing_base_url"}
        try:
            req = urllib.request.Request(url=base_url, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                return {"ok": True, "status": int(resp.status), "error": ""}
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc)[:200]}

    def health(self) -> dict:
        cfg = self.config()
        st = self.state()
        socket_check = self._socket_check(cfg["base_url"])
        http_ping = self._http_ping(cfg["base_url"]) if socket_check.get("reachable") else {"ok": False, "status": 0, "error": "socket_unreachable"}

        return {
            "configured": bool(cfg["base_url"] and cfg["api_key"] and cfg["webhook_path"]),
            "base_url": cfg["base_url"],
            "webhook_path": cfg["webhook_path"],
            "live_enabled": cfg["live_enabled"],
            "verified": bool(st.get("verified", False)),
            "last_verify_status": str(st.get("last_verify_status", "") or ""),
            "last_verify_ts": float(st.get("last_verify_ts", 0.0) or 0.0),
            "last_webhook_status": str(st.get("last_webhook_status", "") or ""),
            "last_webhook_ts": float(st.get("last_webhook_ts", 0.0) or 0.0),
            "socket_check": socket_check,
            "http_ping": http_ping,
            "api_auth_header_name": cfg["api_auth_header_name"],
            "webhook_auth_header_name": cfg["webhook_auth_header_name"],
        }

    def _request_json(self, method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 30) -> tuple[int, str]:
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url=url, data=data, method=method)
        for k, v in headers.items():
            if v:
                req.add_header(k, v)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return int(resp.status), raw

    def verify_api(self) -> dict:
        cfg = self.config()
        st = self.state()

        if not cfg["base_url"] or not cfg["api_key"]:
            st["verified"] = False
            st["last_verify_status"] = "missing_n8n_config"
            st["last_verify_ts"] = time.time()
            self._save_state(st)
            return {"status": "error", "message": "missing n8n config"}

        url = f"{cfg['base_url']}/api/v1/workflows?limit=1"
        try:
            status, raw = self._request_json(
                "GET",
                url,
                headers={cfg["api_auth_header_name"]: cfg["api_key"]},
                body=None,
                timeout=25,
            )
            st["verified"] = (200 <= status < 300)
            st["last_verify_status"] = f"http_{status}"
            st["last_verify_ts"] = time.time()
            self._save_state(st)
            return {"status": "ok", "http_status": status, "raw_preview": raw[:500]}
        except Exception as exc:
            st["verified"] = False
            st["last_verify_status"] = str(exc)[:300]
            st["last_verify_ts"] = time.time()
            self._save_state(st)
            return {"status": "error", "message": str(exc)}

    def test_webhook(self, payload: dict, dry_run: bool = True) -> dict:
        cfg = self.config()
        st = self.state()

        if not cfg["base_url"] or not cfg["webhook_path"]:
            return {"status": "error", "message": "missing webhook config"}

        suffix = "webhook-test" if dry_run else "webhook"
        url = f"{cfg['base_url']}/{suffix}/{cfg['webhook_path']}"
        headers = {}
        if cfg["webhook_secret"]:
            headers[cfg["webhook_auth_header_name"]] = cfg["webhook_secret"]

        try:
            status, raw = self._request_json(
                "POST",
                url,
                headers=headers,
                body=payload,
                timeout=60,
            )
            st["last_webhook_status"] = f"http_{status}"
            st["last_webhook_ts"] = time.time()
            self._save_state(st)
            return {
                "status": "ok",
                "http_status": status,
                "dry_run": dry_run,
                "url": url,
                "raw_preview": raw[:1000],
            }
        except Exception as exc:
            st["last_webhook_status"] = str(exc)[:300]
            st["last_webhook_ts"] = time.time()
            self._save_state(st)
            return {"status": "error", "message": str(exc), "url": url}

    def promote_live(self, enabled: bool) -> dict:
        raw = _load_json_lenient(self.connectors_path, {"connectors": []})
        changed = False
        for item in raw.get("connectors", []):
            if str(item.get("connector_id") or "") == "n8n_primary":
                item.setdefault("metadata", {})
                item["metadata"]["live_enabled"] = bool(enabled)
                changed = True
        _write_json_nobom(self.connectors_path, raw)
        st = self.state()
        st["live_enabled"] = bool(enabled)
        self._save_state(st)
        return {"status": "ok", "live_enabled": bool(enabled), "changed": changed}