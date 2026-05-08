from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, List

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

CONFIG_PATH = Path("/bridge-data/bridge-config.json")
N8N_BASE_URL_DEFAULT = "http://n8n-main:5678"
N8N_EDITOR_BASE_URL_DEFAULT = "http://127.0.0.1:5680"
N8N_WEBHOOK_BASE_URL_DEFAULT = "http://n8n-main:5678"

app = FastAPI(title="Jarvis n8n Bridge", version="1.2.0")


class ConfigureRequest(BaseModel):
    api_key: str
    n8n_base_url: Optional[str] = None
    n8n_editor_base_url: Optional[str] = None
    n8n_webhook_base_url: Optional[str] = None


class CreateWebhookWorkflowRequest(BaseModel):
    name: Optional[str] = None
    webhook_path: Optional[str] = None


class ProbeWebhookRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


def mask_secret(value: str) -> str:
    if not value:
        return "<EMPTY>"
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + ("*" * (len(value) - 8)) + value[-4:]


def load_cfg() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {
            "n8n_base_url": N8N_BASE_URL_DEFAULT,
            "n8n_editor_base_url": N8N_EDITOR_BASE_URL_DEFAULT,
            "n8n_webhook_base_url": N8N_WEBHOOK_BASE_URL_DEFAULT,
            "api_key": "",
        }
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("n8n_base_url", N8N_BASE_URL_DEFAULT)
            data.setdefault("n8n_editor_base_url", N8N_EDITOR_BASE_URL_DEFAULT)
            data.setdefault("n8n_webhook_base_url", N8N_WEBHOOK_BASE_URL_DEFAULT)
            data.setdefault("api_key", "")
            return data
    except Exception:
        pass
    return {
        "n8n_base_url": N8N_BASE_URL_DEFAULT,
        "n8n_editor_base_url": N8N_EDITOR_BASE_URL_DEFAULT,
        "n8n_webhook_base_url": N8N_WEBHOOK_BASE_URL_DEFAULT,
        "api_key": "",
    }


def save_cfg(data: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def n8n_req(method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None, expected: tuple[int, ...] = (200, 201)) -> Dict[str, Any]:
    cfg = load_cfg()
    api_key = cfg.get("api_key") or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="Bridge has no n8n API key configured yet.")

    base = str(cfg.get("n8n_base_url") or N8N_BASE_URL_DEFAULT).rstrip("/")
    url = f"{base}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-N8N-API-KEY": api_key,
    }

    resp = requests.request(
        method=method.upper(),
        url=url,
        headers=headers,
        json=json_body,
        timeout=30,
        allow_redirects=False,
    )

    try:
        body = resp.json()
    except Exception:
        body = resp.text

    result = {
        "url": url,
        "status_code": resp.status_code,
        "ok": resp.status_code in expected,
        "body": body,
    }

    if resp.status_code not in expected:
        raise HTTPException(status_code=resp.status_code, detail=result)

    return result


def extract_workflow_id(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ("id", "workflowId"):
            if payload.get(key) is not None:
                return str(payload[key])
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("id", "workflowId"):
                if data.get(key) is not None:
                    return str(data[key])
    return None


@app.get("/health")
def health() -> Dict[str, Any]:
    cfg = load_cfg()

    main_readiness = {"ok": False, "url": "http://n8n-main:5678/healthz/readiness"}
    try:
        r = requests.get(main_readiness["url"], timeout=5)
        main_readiness["status_code"] = r.status_code
        main_readiness["ok"] = 200 <= r.status_code < 300
    except Exception as exc:
        main_readiness["error"] = f"{exc.__class__.__name__}: {exc}"

    return {
        "status": "ok",
        "service": "jarvis_n8n_bridge",
        "config_path": str(CONFIG_PATH),
        "n8n_base_url": cfg.get("n8n_base_url"),
        "n8n_editor_base_url": cfg.get("n8n_editor_base_url"),
        "n8n_webhook_base_url": cfg.get("n8n_webhook_base_url"),
        "has_api_key": bool(cfg.get("api_key")),
        "api_key_preview": mask_secret(cfg.get("api_key", "")),
        "n8n_main_readiness": main_readiness,
    }


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    cfg = load_cfg()
    return {
        "n8n_base_url": cfg.get("n8n_base_url"),
        "n8n_editor_base_url": cfg.get("n8n_editor_base_url"),
        "n8n_webhook_base_url": cfg.get("n8n_webhook_base_url"),
        "has_api_key": bool(cfg.get("api_key")),
        "api_key_preview": mask_secret(cfg.get("api_key", "")),
        "config_path": str(CONFIG_PATH),
    }


@app.post("/api/configure")
def configure(request: ConfigureRequest) -> Dict[str, Any]:
    cfg = load_cfg()
    cfg["api_key"] = request.api_key.strip()
    if request.n8n_base_url:
        cfg["n8n_base_url"] = request.n8n_base_url.rstrip("/")
    if request.n8n_editor_base_url:
        cfg["n8n_editor_base_url"] = request.n8n_editor_base_url.rstrip("/")
    if request.n8n_webhook_base_url:
        cfg["n8n_webhook_base_url"] = request.n8n_webhook_base_url.rstrip("/")
    save_cfg(cfg)
    return {
        "status": "ok",
        "has_api_key": bool(cfg.get("api_key")),
        "api_key_preview": mask_secret(cfg.get("api_key", "")),
        "n8n_base_url": cfg.get("n8n_base_url"),
        "n8n_editor_base_url": cfg.get("n8n_editor_base_url"),
        "n8n_webhook_base_url": cfg.get("n8n_webhook_base_url"),
    }


@app.get("/api/n8n/public-api-check")
def public_api_check() -> Dict[str, Any]:
    return n8n_req("GET", "/api/v1/workflows?limit=1", expected=(200,))


@app.get("/api/n8n/workflows")
def list_workflows(limit: int = 50) -> Dict[str, Any]:
    return n8n_req("GET", f"/api/v1/workflows?limit={limit}", expected=(200,))


@app.post("/api/n8n/workflows/create-webhook")
def create_webhook_workflow(request: CreateWebhookWorkflowRequest) -> Dict[str, Any]:
    stamp = str(int(time.time()))
    name = request.name or f"jarvis-local-{stamp}"
    path = (request.webhook_path or f"jarvis-local-{stamp}").strip("/")

    body = {
        "name": name,
        "nodes": [
            {
                "name": "Jarvis Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1,
                "position": [600, 300],
                "parameters": {
                    "httpMethod": "POST",
                    "path": path,
                    "options": {},
                },
            }
        ],
        "connections": {},
        "settings": {},
    }

    created = n8n_req("POST", "/api/v1/workflows", json_body=body, expected=(200, 201))
    wid = extract_workflow_id(created.get("body"))
    return {
        "created": created,
        "workflow_id": wid,
        "webhook_path": path,
    }


@app.post("/api/n8n/workflows/activate/{workflow_id}")
def activate_workflow(workflow_id: str) -> Dict[str, Any]:
    return n8n_req("POST", f"/api/v1/workflows/{workflow_id}/activate", expected=(200, 201, 204))


@app.post("/api/n8n/probe/{webhook_path}")
def probe_webhook(webhook_path: str, request: ProbeWebhookRequest) -> Dict[str, Any]:
    cfg = load_cfg()

    path = webhook_path.strip("/")
    body = request.payload or {
        "source": "jarvis_n8n_bridge",
        "message": "hello",
        "ts": time.time(),
    }

    candidates: List[str] = []
    for base in [
        cfg.get("n8n_webhook_base_url"),
        cfg.get("n8n_base_url"),
        "http://n8n-main:5678",
        "http://127.0.0.1:5680",
    ]:
        if base:
            url = f"{str(base).rstrip('/')}/webhook/{path}"
            if url not in candidates:
                candidates.append(url)

    attempts = []
    for url in candidates:
        try:
            resp = requests.post(url, json=body, timeout=20, allow_redirects=False)
            try:
                resp_body = resp.json()
            except Exception:
                resp_body = resp.text

            attempt = {
                "url": url,
                "status_code": resp.status_code,
                "ok": 200 <= resp.status_code < 300,
                "body": resp_body,
            }
            attempts.append(attempt)

            if attempt["ok"]:
                return {
                    "ok": True,
                    "winner": attempt,
                    "attempts": attempts,
                }
        except Exception as exc:
            attempts.append({
                "url": url,
                "ok": False,
                "error": f"{exc.__class__.__name__}: {exc}",
            })

    raise HTTPException(
        status_code=500,
        detail={
            "message": "All webhook probe attempts failed",
            "attempts": attempts,
        },
    )