from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.supervisor_automation_runtime import get_supervisor_automation_runtime

router = APIRouter(tags=["supervisor_automation"])


class SupervisorAutomationRequest(BaseModel):
    action: str = "echo"
    intent: str = "automation"
    payload: Any = Field(default_factory=dict)
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    source: str = "jarvis"
    use_test_webhook: bool = False
    dry_run: bool = False


@router.get("/api/supervisor/automation/health")
def supervisor_automation_health() -> Dict[str, Any]:
    runtime = get_supervisor_automation_runtime()
    bridge_health = runtime.preview(
        action="health_check",
        intent="health_check",
        payload={},
        mission_id="health-check",
        task_id="health-check",
        source="jarvis",
        use_test_webhook=False,
    )
    return {
        "status": "ok",
        "route": "n8n",
        "config": {
            "artifact_dir": str(runtime.config.artifact_dir),
            "max_retries": runtime.config.max_retries,
            "retry_delay_ms": runtime.config.retry_delay_ms,
            "require_n8n_health": runtime.config.require_n8n_health,
        },
        "preview": bridge_health,
    }


@router.post("/api/supervisor/automation/preview")
def supervisor_automation_preview(request: SupervisorAutomationRequest) -> Dict[str, Any]:
    runtime = get_supervisor_automation_runtime()
    return runtime.preview(
        action=request.action,
        intent=request.intent,
        payload=request.payload,
        mission_id=request.mission_id,
        task_id=request.task_id,
        source=request.source,
        use_test_webhook=request.use_test_webhook,
    )


@router.post("/api/supervisor/automation/run")
def supervisor_automation_run(request: SupervisorAutomationRequest) -> Dict[str, Any]:
    runtime = get_supervisor_automation_runtime()

    if request.dry_run:
        return {
            "dry_run": True,
            "preview": runtime.preview(
                action=request.action,
                intent=request.intent,
                payload=request.payload,
                mission_id=request.mission_id,
                task_id=request.task_id,
                source=request.source,
                use_test_webhook=request.use_test_webhook,
            ),
        }

    try:
        return runtime.execute(
            action=request.action,
            intent=request.intent,
            payload=request.payload,
            mission_id=request.mission_id,
            task_id=request.task_id,
            source=request.source,
            use_test_webhook=request.use_test_webhook,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc