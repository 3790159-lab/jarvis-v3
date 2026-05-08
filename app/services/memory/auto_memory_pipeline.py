from __future__ import annotations

from app.models.auto_memory import (
    AutoMemoryMissionPayload,
    AutoMemoryRunResponse,
)
from app.models.obsidian_bridge import AutoMissionMemoryRequest
from app.services.memory.memory_analysis_service import MemoryAnalysisService
from app.services.memory.obsidian_bridge_service import ObsidianBridgeService


class AutoMemoryPipeline:
    def __init__(self) -> None:
        self.analysis_service = MemoryAnalysisService()
        self.obsidian_bridge = ObsidianBridgeService()

    def run(self, payload: AutoMemoryMissionPayload) -> AutoMemoryRunResponse:
        analysis = self.analysis_service.analyze(payload)

        write_result = self.obsidian_bridge.auto_write_mission_memory(
            AutoMissionMemoryRequest(
                mission_id=analysis.mission_id,
                objective=analysis.objective,
                summary=analysis.generated_summary,
                lessons_learned=analysis.lessons_learned,
                known_issues=analysis.known_issues,
                tags=analysis.tags,
                metadata={
                    "source": "auto_memory_pipeline",
                    "mission_status": payload.status,
                    **(payload.metadata or {}),
                },
            )
        )

        return AutoMemoryRunResponse(
            ok=write_result.ok,
            mission_id=payload.mission_id,
            analysis=analysis,
            semantic_files=write_result.semantic_files,
            obsidian_files=write_result.obsidian_files,
            error=write_result.error,
        )
