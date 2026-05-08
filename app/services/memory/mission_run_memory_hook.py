from __future__ import annotations

from typing import Any, Dict, List

from app.models.auto_memory import AutoMemoryMissionPayload, AutoMemoryTaskResult
from app.models.mission_run_memory import MissionRunMemoryResponse
from app.services.memory.auto_memory_pipeline import AutoMemoryPipeline


class MissionRunMemoryHook:
    def __init__(self) -> None:
        self.pipeline = AutoMemoryPipeline()

    def handle(self, mission_result: Dict[str, Any], objective: str | None = None, metadata: Dict[str, Any] | None = None) -> MissionRunMemoryResponse:
        metadata = metadata or {}

        mission_id = str(
            mission_result.get("mission_id")
            or mission_result.get("id")
            or metadata.get("mission_id")
            or "unknown_mission"
        )

        status = str(mission_result.get("status", "unknown"))
        summary = str(mission_result.get("summary", "") or "")

        task_results_raw = mission_result.get("task_results", []) or []
        task_results: List[AutoMemoryTaskResult] = []

        for item in task_results_raw:
            if not isinstance(item, dict):
                continue
            task_results.append(
                AutoMemoryTaskResult(
                    task_id=item.get("task_id"),
                    title=item.get("title", "") or item.get("name", "") or "",
                    status=item.get("status", "") or "unknown",
                    message=item.get("message", "") or item.get("summary", "") or "",
                    output=item.get("output", {}) if isinstance(item.get("output", {}), dict) else {},
                )
            )

        resolved_objective = (
            objective
            or mission_result.get("objective")
            or metadata.get("objective")
            or f"Mission run for {mission_id}"
        )

        payload = AutoMemoryMissionPayload(
            mission_id=mission_id,
            objective=str(resolved_objective),
            status=status,
            summary=summary,
            task_results=task_results,
            metadata={
                "source": "mission_run_memory_hook",
                **metadata,
            },
        )

        pipeline_result = self.pipeline.run(payload)

        return MissionRunMemoryResponse(
            ok=pipeline_result.ok,
            mission_id=mission_id,
            memory_written=pipeline_result.ok,
            semantic_files=pipeline_result.semantic_files,
            obsidian_files=pipeline_result.obsidian_files,
            analysis=pipeline_result.analysis.model_dump(),
            error=pipeline_result.error,
        )
