from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.google_workspace_tools import (
    healthcheck,
    gmail_list_messages,
    gmail_send_email,
    calendar_list_events,
    calendar_create_event,
)

router = APIRouter(prefix="/api/google", tags=["google"])


class GoogleExecuteRequest(BaseModel):
    action: str = Field(..., description="Action name")
    payload: Dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
def google_health() -> Dict[str, Any]:
    return healthcheck()


@router.post("/execute")
def google_execute(request: GoogleExecuteRequest) -> Dict[str, Any]:
    action = (request.action or "").strip()
    payload = request.payload or {}

    try:
        if action == "gmail_list_messages":
            return gmail_list_messages(
                query=str(payload.get("query", "")),
                max_results=int(payload.get("max_results", 10)),
                user_id=str(payload.get("user_id", "me")),
                interactive_oauth_fallback=bool(payload.get("interactive_oauth_fallback", False)),
            )

        if action == "gmail_send_email":
            attachments = payload.get("attachments") or []
            cc = payload.get("cc") or []
            bcc = payload.get("bcc") or []

            return gmail_send_email(
                to=str(payload.get("to", "")),
                subject=str(payload.get("subject", "")),
                body_text=str(payload.get("body_text", "")),
                cc=cc,
                bcc=bcc,
                attachments=attachments,
                user_id=str(payload.get("user_id", "me")),
                interactive_oauth_fallback=bool(payload.get("interactive_oauth_fallback", False)),
            )

        if action == "calendar_list_events":
            return calendar_list_events(
                calendar_id=payload.get("calendar_id"),
                time_min=payload.get("time_min"),
                time_max=payload.get("time_max"),
                max_results=int(payload.get("max_results", 10)),
                q=payload.get("q"),
                interactive_oauth_fallback=bool(payload.get("interactive_oauth_fallback", False)),
            )

        if action == "calendar_create_event":
            attendees = payload.get("attendees") or []

            return calendar_create_event(
                summary=str(payload.get("summary", "")),
                start_iso=str(payload.get("start_iso", "")),
                end_iso=str(payload.get("end_iso", "")),
                calendar_id=payload.get("calendar_id"),
                timezone_name=payload.get("timezone_name"),
                description=str(payload.get("description", "")),
                location=str(payload.get("location", "")),
                attendees=attendees,
                interactive_oauth_fallback=bool(payload.get("interactive_oauth_fallback", False)),
            )

        raise HTTPException(status_code=400, detail=f"Unsupported google action: {action}")

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
