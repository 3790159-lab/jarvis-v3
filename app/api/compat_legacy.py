from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["compat_legacy"])


def _backend_base() -> str:
    # Hardened runtime base resolution for unified cloud proxy.
    explicit = (os.getenv("BACKEND_BASE_URL") or "").strip()
    if explicit:
        if explicit in ("http://127.0.0.1:8010", "http://localhost:8010"):
            return "http://127.0.0.1:8015"
        return explicit.rstrip("/")

    host = (os.getenv("APP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (os.getenv("APP_PORT") or "8015").strip() or "8015"

    if port == "8010":
        port = "8015"

    return f"http://{host}:{port}"

def _call(method: str, path: str, payload: Optional[dict] = None, timeout: int = 120):
    url = _backend_base() + path
    method = method.upper().strip()

    try:
        if method == "GET":
            response = requests.get(url, timeout=timeout)
        elif method == "POST":
            response = requests.post(url, json=payload or {}, timeout=timeout)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported method: {method}")

        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise HTTPException(
                status_code=response.status_code,
                detail={
                    "path": path,
                    "status_code": response.status_code,
                    "response": detail,
                },
            )

        if "application/json" in content_type:
            return response.json()

        return {
            "status": "ok",
            "text": response.text,
        }
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Proxy call failed for {path}: {exc}") from exc


class RespondCompatRequest(BaseModel):
    message: str
    user_id: Optional[str] = "compat_user"
    mode: Optional[str] = None
    mission_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GoalCompatRequest(BaseModel):
    objective: str
    constraints: list[str] = Field(default_factory=list)
    summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


@router.get("/api/agents")
def compat_agents():
    registry = None
    adapters = None

    try:
        registry = _call("GET", "/api/agents/registry", timeout=30)
    except Exception as exc:
        registry = {"status": "error", "detail": str(exc)}

    try:
        adapters = _call("GET", "/api/agents/adapters", timeout=30)
    except Exception as exc:
        adapters = {"status": "error", "detail": str(exc)}

    return {
        "status": "ok",
        "registry": registry,
        "adapters": adapters,
    }


@router.post("/api/respond")
def compat_respond(request: RespondCompatRequest):
    text = (request.message or "").strip()

    # Send several common aliases to maximize compatibility with older /respond handlers.
    payload = {
        "message": text,
        "text": text,
        "prompt": text,
        "query": text,
        "input": text,
        "user_id": request.user_id,
        "mode": request.mode,
        "mission_id": request.mission_id,
        "metadata": request.metadata,
        "payload": {
            "message": text,
            "text": text,
            "prompt": text,
            "query": text,
            "input": text,
            "user_id": request.user_id,
            "mode": request.mode,
            "mission_id": request.mission_id,
            "metadata": request.metadata,
        },
    }

    return _call("POST", "/respond", payload=payload, timeout=120)

@router.get("/api/goals")
def compat_goals_info():
    return {
        "status": "ok",
        "message": "Compatibility endpoint is active.",
        "preferred_routes": [
            "/api/runtime-bridge/submit",
            "/api/autonomy/missions",
            "/api/mission-graph/submit",
        ],
    }


@router.post("/api/goals")
def compat_goals_create(request: GoalCompatRequest):
    attempts = [
        ("/api/runtime-bridge/submit", request.model_dump()),
        (
            "/api/autonomy/missions",
            {
                "objective": request.objective,
                "constraints": request.constraints,
                "summary": request.summary,
                "metadata": request.metadata,
            },
        ),
        (
            "/api/mission-graph/submit",
            {
                "objective": request.objective,
                "constraints": request.constraints,
                "summary": request.summary,
                "metadata": request.metadata,
            },
        ),
    ]

    errors = []
    for path, body in attempts:
        try:
            result = _call("POST", path, payload=body, timeout=120)
            return {
                "status": "ok",
                "compat_route": "/api/goals",
                "delegated_to": path,
                "result": result,
            }
        except Exception as exc:
            errors.append({"path": path, "detail": str(exc)})

    raise HTTPException(
        status_code=502,
        detail={
            "message": "All goal creation compatibility delegates failed",
            "attempts": errors,
        },
    )