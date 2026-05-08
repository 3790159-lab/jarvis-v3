from typing import Any, Dict, Optional
from fastapi import APIRouter
from pydantic import BaseModel

from app.services.supervisor_core import debug_routes_snapshot, handle_supervisor_message

router = APIRouter()

class RespondRequest(BaseModel):
    text: str = ""
    chat_id: Optional[str] = None
    source: Optional[str] = None

class AssistantReply(BaseModel):
    reply: str
    mode: str = "smart_local"
    source: str = "local_fallback"
    confidence: float = 0.90
    route: str = "chat"
    mission: Optional[Dict[str, Any]] = None

@router.post("/respond", response_model=AssistantReply)
def respond(payload: RespondRequest):
    result = handle_supervisor_message(payload.text or "", source=payload.source or "api")
    return AssistantReply(
        reply=result.get("reply", "No reply generated."),
        mode=result.get("mode", "smart_local"),
        source=result.get("source", "local_fallback"),
        confidence=float(result.get("confidence", 0.75)),
        route=result.get("route", "chat"),
        mission=result.get("mission"),
    )

@router.post("/routes/debug")
def debug_route(payload: RespondRequest):
    return debug_routes_snapshot(payload.text or "")