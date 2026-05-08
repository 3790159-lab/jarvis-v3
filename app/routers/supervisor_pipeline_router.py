from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.supervisor_pipeline_runtime import get_supervisor_pipeline_runtime

router = APIRouter(tags=["supervisor_pipeline"])


class PipelineStep(BaseModel):
    step_id: Optional[str] = None
    action: str
    intent: Optional[str] = None
    payload: Any = Field(default_factory=dict)
    source: str = "jarvis"
    use_test_webhook: bool = False
    continue_on_error: bool = False


class SupervisorPipelineRequest(BaseModel):
    pipeline_name: str = "jarvis_pipeline"
    mission_id: Optional[str] = None
    dry_run: bool = False
    steps: List[PipelineStep]


@router.get("/api/supervisor/pipeline/health")
def supervisor_pipeline_health() -> Dict[str, Any]:
    runtime = get_supervisor_pipeline_runtime()
    return {
        "status": "ok",
        "artifact_dir": str(runtime.config.artifact_dir),
        "max_steps": runtime.config.max_steps,
    }


@router.post("/api/supervisor/pipeline/preview")
def supervisor_pipeline_preview(request: SupervisorPipelineRequest) -> Dict[str, Any]:
    runtime = get_supervisor_pipeline_runtime()
    return runtime.preview(
        pipeline_name=request.pipeline_name,
        steps=[step.model_dump() for step in request.steps],
        mission_id=request.mission_id,
    )


@router.post("/api/supervisor/pipeline/run")
def supervisor_pipeline_run(request: SupervisorPipelineRequest) -> Dict[str, Any]:
    runtime = get_supervisor_pipeline_runtime()

    if request.dry_run:
        return {
            "dry_run": True,
            "preview": runtime.preview(
                pipeline_name=request.pipeline_name,
                steps=[step.model_dump() for step in request.steps],
                mission_id=request.mission_id,
            ),
        }

    try:
        return runtime.run(
            pipeline_name=request.pipeline_name,
            steps=[step.model_dump() for step in request.steps],
            mission_id=request.mission_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc