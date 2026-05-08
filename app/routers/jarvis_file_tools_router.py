from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.file_parsers import parse_file, summarize_parse_result

router = APIRouter(prefix="/api/jarvis/files", tags=["jarvis-files"])


class ParseRequest(BaseModel):
    path: str
    mime_type: str = ""


class ExtractRequest(BaseModel):
    path: str
    mime_type: str = ""
    instruction: str = "Extract key structured data"


@router.post("/parse")
async def parse_endpoint(req: ParseRequest) -> Dict[str, Any]:
    if not Path(req.path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")
    result = parse_file(req.path, req.mime_type)
    result["summary"] = summarize_parse_result(result, Path(req.path).name)
    return result


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "service": "jarvis_file_tools"}
