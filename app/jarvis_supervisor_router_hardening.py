from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MODULE_ID = "jarvis_supervisor_router_hardening"
MODULE_VERSION = "1.0.0"

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def install(app: FastAPI) -> None:
    if getattr(app.state, "_jarvis_supervisor_router_hardening_installed", False):
        return

    app.state._jarvis_supervisor_router_hardening_installed = True
    app.state._jarvis_supervisor_router_hardening_installed_at = now_iso()

    @app.middleware("http")
    async def _jarvis_request_id_middleware(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        started = time.time()
        try:
            response = await call_next(request)
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "service": "jarvis_v3_supervisor",
                    "request_id": request_id,
                    "detail": "internal_error",
                    "exception_type": type(exc).__name__,
                },
            )
        response.headers["X-Jarvis-Request-Id"] = request_id
        response.headers["X-Jarvis-Elapsed-Ms"] = str(int((time.time() - started) * 1000))
        return response

    @app.get("/healthz")
    def _jarvis_healthz() -> Dict[str, Any]:
        return {
            "status": "healthy",
            "service": "jarvis_v3_supervisor",
            "hardening": "enabled",
            "installed_at": app.state._jarvis_supervisor_router_hardening_installed_at,
            "route_count": len(app.routes),
        }

    @app.get("/_jarvis/router/status")
    def _jarvis_router_status() -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "jarvis_v3_supervisor",
            "module_id": MODULE_ID,
            "version": MODULE_VERSION,
            "installed_at": app.state._jarvis_supervisor_router_hardening_installed_at,
            "route_count": len(app.routes),
            "request_id_header": "X-Jarvis-Request-Id",
            "elapsed_header": "X-Jarvis-Elapsed-Ms",
        }