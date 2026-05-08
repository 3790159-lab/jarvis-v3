from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.jarvis_telegram_file_tools import build_internet_table

router = APIRouter(prefix="/api/jarvis/telegram-tools", tags=["jarvis-telegram-tools"])


class InternetTableRequest(BaseModel):
    query: str
    max_results: int = Field(default=8)
    send_to_telegram: bool = Field(default=True)


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "jarvis_telegram_file_tools",
        "tools": [
            "telegram.internet_table",
            "telegram.send_file"
        ]
    }


@router.post("/internet-table")
def internet_table(req: InternetTableRequest) -> Dict[str, Any]:
    return build_internet_table(req.query, req.max_results, req.send_to_telegram)