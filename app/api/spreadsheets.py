from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.spreadsheet_service import execute_spreadsheet_job


router = APIRouter(prefix="/api/spreadsheets", tags=["spreadsheets"])


class SpreadsheetExecuteRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
def spreadsheets_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "feature": "spreadsheets",
        "modes": ["local_excel", "google_sheets"],
        "actions": [
            "create_table",
            "append_rows",
            "update_cells",
            "format_header",
            "inspect",
            "probe",
            "oauth_bootstrap",
        ],
    }


@router.post("/execute")
def spreadsheets_execute(request: SpreadsheetExecuteRequest) -> Dict[str, Any]:
    try:
        return execute_spreadsheet_job(request.payload)
    except Exception as exc:
        message = str(exc).strip()
        if not message:
            message = f"{exc.__class__.__name__}: empty error message"

        raise HTTPException(
            status_code=400,
            detail={
                "error_type": exc.__class__.__name__,
                "message": message,
                "mode": request.payload.get("mode"),
                "action": request.payload.get("action"),
            },
        ) from exc
