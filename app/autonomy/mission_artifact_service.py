from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_builders import build_artifact
from .artifact_models import ArtifactTaskRequest
from .mission_artifact_classifier import build_plan
from .mission_artifact_models import (
    MissionArtifactRequest,
    MissionArtifactRunRecord,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class MissionArtifactService:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.runs_root = self.project_root / "artifacts" / "mission_runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def classify(self, request: MissionArtifactRequest) -> dict[str, Any]:
        plan = build_plan(request)
        return {
            "objective": request.objective,
            "selected_task_type": plan.selected_task_type.value,
            "artifact_name": plan.artifact_name,
            "description": plan.description,
            "payload": plan.payload,
            "candidates": [candidate.model_dump() for candidate in plan.candidates],
            "reasoning": plan.reasoning,
        }

    def execute(self, request: MissionArtifactRequest) -> dict[str, Any]:
        plan = build_plan(request)
        run_id = f"mission_artifact_{uuid.uuid4().hex[:12]}"

        artifact_request = ArtifactTaskRequest(
            task_type=plan.selected_task_type,
            name=plan.artifact_name,
            description=plan.description,
            payload=plan.payload,
            overwrite=False,
        )

        artifact_result = build_artifact(self.project_root, artifact_request)

        summary = {
            "objective": request.objective,
            "selected_task_type": plan.selected_task_type.value,
            "artifact_name": plan.artifact_name,
            "artifact_status": artifact_result.get("status"),
            "artifact_ok": artifact_result.get("ok", False),
            "output_dir": artifact_result.get("output_dir"),
            "manifest_path": artifact_result.get("manifest_path"),
            "files_count": len(artifact_result.get("files", [])),
        }

        record = MissionArtifactRunRecord(
            run_id=run_id,
            status="completed" if artifact_result.get("ok") else "failed",
            objective=request.objective,
            selected_task_type=plan.selected_task_type.value,
            artifact_name=plan.artifact_name,
            created_at=_now_iso(),
            plan={
                "selected_task_type": plan.selected_task_type.value,
                "artifact_name": plan.artifact_name,
                "description": plan.description,
                "payload": plan.payload,
                "candidates": [candidate.model_dump() for candidate in plan.candidates],
                "reasoning": plan.reasoning,
            },
            artifact_result=artifact_result,
            summary=summary,
        )

        run_path = self.runs_root / f"{run_id}.json"
        _write_json(run_path, record.model_dump())

        return {
            "ok": artifact_result.get("ok", False),
            "run_id": run_id,
            "status": record.status,
            "objective": request.objective,
            "selected_task_type": plan.selected_task_type.value,
            "artifact_name": plan.artifact_name,
            "plan": record.plan,
            "artifact_result": artifact_result,
            "summary": summary,
            "run_record_path": str(run_path),
        }
