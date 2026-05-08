"""Public API v1 — Phase 44.

Endpoints:
  POST /api/v1/ask               — ask Jarvis a question
  GET  /api/v1/agents            — list available agents/capabilities
  GET  /api/v1/status            — service status
  POST /api/v1/admin/api-keys    — create API key (admin only)

Auth: X-API-Key header or ?api_key= query param.
Rate limit: 10 req/min per key (in-memory, resets on restart).
"""
from __future__ import annotations

import json
import os
import secrets
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["public-api-v1"])

_ROOT = Path(__file__).parent.parent.parent
_API_KEYS_PATH = _ROOT / "state" / "api_keys.json"

# ── Rate limiting (in-memory) ──────────────────────────────────────────────

_RATE_WINDOW = 60  # seconds
_RATE_LIMIT = 10   # requests per window

# {api_key: [(timestamp, ...), ...]}
_rate_store: dict = defaultdict(list)


def _check_rate_limit(api_key: str) -> bool:
    """Return True if under limit, False if exceeded."""
    now = time.time()
    calls = _rate_store[api_key]
    # Remove calls outside window
    calls[:] = [t for t in calls if now - t < _RATE_WINDOW]
    if len(calls) >= _RATE_LIMIT:
        return False
    calls.append(now)
    return True


# ── API key management ─────────────────────────────────────────────────────

def _load_keys() -> Dict[str, Any]:
    try:
        if _API_KEYS_PATH.exists():
            return json.loads(_API_KEYS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_keys(keys: Dict[str, Any]) -> None:
    _API_KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _API_KEYS_PATH.write_text(json.dumps(keys, indent=2, ensure_ascii=False), encoding="utf-8")


def validate_api_key(api_key: str) -> bool:
    """Return True if api_key is valid and active."""
    if not api_key:
        return False
    keys = _load_keys()
    entry = keys.get(api_key)
    if not entry:
        return False
    return entry.get("active", True)


def _get_admin_key() -> str:
    return os.getenv("JARVIS_ADMIN_KEY", "").strip()


def _is_admin(api_key: str) -> bool:
    admin = _get_admin_key()
    return bool(admin and api_key == admin)


# ── Auth helper ────────────────────────────────────────────────────────────

def _extract_api_key(request: Request) -> str:
    return (
        request.headers.get("X-API-Key")
        or request.query_params.get("api_key")
        or ""
    )


# ── Request/response models ────────────────────────────────────────────────

class AskRequest(BaseModel):
    query: str
    timeout: int = 60


class CreateKeyRequest(BaseModel):
    label: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/ask")
async def ask(req: AskRequest, request: Request) -> Dict[str, Any]:
    """Ask Jarvis a question. Returns AI response."""
    api_key = _extract_api_key(request)

    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    if not _check_rate_limit(api_key):
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded: {_RATE_LIMIT} req/{_RATE_WINDOW}s")

    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    response_text = await _run_query(query, req.timeout)
    return {"response": response_text, "query": query, "ok": True}


@router.get("/agents")
async def list_agents(request: Request) -> Dict[str, Any]:
    """List available Jarvis agents and capabilities."""
    api_key = _extract_api_key(request)
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    agents = [
        {"id": "research", "description": "Internet research via Perplexity/Tavily"},
        {"id": "brain", "description": "Multi-step reasoning and planning"},
        {"id": "engineer", "description": "AI Engineer: architecture review"},
        {"id": "simple_question", "description": "Fast knowledge-base answers"},
        {"id": "generate", "description": "Image generation via Replicate FLUX 1.1"},
        {"id": "table", "description": "Structured table generation"},
    ]
    return {"agents": agents, "count": len(agents)}


@router.get("/status")
async def public_status(request: Request) -> Dict[str, Any]:
    """Public service status."""
    api_key = _extract_api_key(request)
    if not validate_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    from app.routers.jarvis_dashboard_router import _get_status
    return _get_status()


@router.post("/admin/api-keys")
async def create_api_key(req: CreateKeyRequest, request: Request) -> Dict[str, Any]:
    """Create a new API key (requires JARVIS_ADMIN_KEY header)."""
    api_key = _extract_api_key(request)
    if not _is_admin(api_key):
        raise HTTPException(status_code=403, detail="Admin key required")

    new_key = "jv1_" + secrets.token_urlsafe(24)
    keys = _load_keys()
    keys[new_key] = {
        "label": req.label,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "active": True,
    }
    _save_keys(keys)
    return {"api_key": new_key, "label": req.label, "ok": True}


@router.get("/admin/api-keys")
async def list_api_keys(request: Request) -> Dict[str, Any]:
    """List all API keys (requires JARVIS_ADMIN_KEY header)."""
    api_key = _extract_api_key(request)
    if not _is_admin(api_key):
        raise HTTPException(status_code=403, detail="Admin key required")

    keys = _load_keys()
    summary = [
        {"key_prefix": k[:10] + "...", "label": v.get("label"), "active": v.get("active"), "created_at": v.get("created_at")}
        for k, v in keys.items()
    ]
    return {"keys": summary, "count": len(summary)}


# ── Internal query runner ──────────────────────────────────────────────────

async def _run_query(query: str, timeout: int = 60) -> str:
    import urllib.request
    backend = (
        os.getenv("BACKEND_BASE_URL") or os.getenv("TELEGRAM_BACKEND_URL") or "http://127.0.0.1:8010"
    ).rstrip("/")
    payload = json.dumps({"query": query}, ensure_ascii=False).encode()
    try:
        req = urllib.request.Request(
            f"{backend}/api/jarvis/tools/internet/research",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data.get("answer") or data.get("result") or "No answer returned."
    except Exception as e:
        return f"Error: {e}"
