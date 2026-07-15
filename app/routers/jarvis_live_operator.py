from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.services.api_auth import require_api_key
from app.services.jarvis_live_operator_brain import JarvisLiveOperatorBrain


router = APIRouter(prefix="/api/jarvis", tags=["jarvis-live-operator"])


class LiveCommandRequest(BaseModel):
    text: Optional[str] = None
    message: Optional[str] = None
    prompt: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


@router.get("/live-health")
def live_health() -> Dict[str, Any]:
    return JarvisLiveOperatorBrain().health().__dict__


@router.post("/live-command")
def live_command(
    req: LiveCommandRequest, _key: str = Depends(require_api_key)
) -> Dict[str, Any]:
    text = req.text or req.message or req.prompt or ""
    return JarvisLiveOperatorBrain().handle(text=text, payload=req.payload or {})


# This endpoint is useful if your Telegram/operator panel can be pointed here.
@router.post("/respond-live")
def respond_live(
    req: LiveCommandRequest, _key: str = Depends(require_api_key)
) -> Dict[str, Any]:
    text = req.text or req.message or req.prompt or ""
    result = JarvisLiveOperatorBrain().handle(text=text, payload=req.payload or {})
    return {
        "ok": result.get("ok", False),
        "reply": result.get("message", ""),
        "result": result,
    }