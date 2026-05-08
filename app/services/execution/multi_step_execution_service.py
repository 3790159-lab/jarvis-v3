from __future__ import annotations

import uuid
from typing import List

from app.models.ai_router import TaskType
from app.models.external_executor import ExternalExecutorRequest
from app.models.multi_step_execution import (
    ExecutionStep,
    MultiStepExecutionRequest,
    MultiStepExecutionResponse,
)
from app.models.obsidian_bridge import AutoMissionMemoryRequest
from app.services.ai.external_executor_service import ExternalExecutorService
from app.services.memory.obsidian_bridge_service import ObsidianBridgeService


class MultiStepExecutionService:
    def __init__(self) -> None:
        self.executor = ExternalExecutorService()
        self.memory = ObsidianBridgeService()

    async def execute(self, request: MultiStepExecutionRequest) -> MultiStepExecutionResponse:
        mission_id = f"mission_multistep_{uuid.uuid4().hex[:8]}"

        steps: List[ExecutionStep] = [
            ExecutionStep(
                step_id="plan",
                title="Plan execution approach",
                description="Create a concise execution plan for the requested objective.",
                task_type=TaskType.REASONING,
                preferred_provider="anthropic",
                metadata={"phase": "plan"},
            ),
            ExecutionStep(
                step_id="execute",
                title="Execute core implementation",
                description=request.description or request.title,
                task_type=TaskType.CODING,
                preferred_provider="openai",
                metadata={"phase": "execute"},
            ),
            ExecutionStep(
                step_id="validate",
                title="Validate execution result",
                description="Review the produced result, identify risks, and suggest final improvements.",
                task_type=TaskType.REASONING,
                preferred_provider="anthropic",
                metadata={"phase": "validate"},
            ),
        ]

        for step in steps:
            result = await self.executor.execute(
                ExternalExecutorRequest(
                    title=step.title,
                    objective=request.objective,
                    description=step.description,
                    task_type=step.task_type,
                    preferred_provider=step.preferred_provider,  # string accepted upstream via API model path
                    dry_run=False,
                    metadata={
                        "mission_id": mission_id,
                        "step_id": step.step_id,
                        **request.metadata,
                        **step.metadata,
                    },
                )
            )

            if result.ok:
                step.status = "completed"
                step.response_text = result.response_text
            else:
                step.status = "failed"
                step.error = result.error or "Unknown step execution error"

        failed_steps = [s for s in steps if s.status == "failed"]
        ok = len(failed_steps) == 0

        final_summary = self._build_summary(request, steps, ok)

        memory_written = False
        if request.use_memory:
            memory_result = self.memory.auto_write_mission_memory(
                AutoMissionMemoryRequest(
                    mission_id=mission_id,
                    objective=request.objective,
                    summary=final_summary,
                    lessons_learned=[
                        "Multi-step execution works better when plan, execute, and validate are separated.",
                        "Provider specialization improves quality across different execution phases.",
                    ],
                    known_issues=[
                        s.error for s in steps if s.error
                    ],
                    tags=["jarvis", "multi-step", "execution"],
                    metadata=request.metadata,
                )
            )
            memory_written = memory_result.ok

        return MultiStepExecutionResponse(
            ok=ok,
            mission_id=mission_id,
            objective=request.objective,
            steps=steps,
            final_summary=final_summary,
            memory_written=memory_written,
            error=None if ok else "One or more execution steps failed",
        )

    @staticmethod
    def _build_summary(request: MultiStepExecutionRequest, steps: List[ExecutionStep], ok: bool) -> str:
        completed = len([s for s in steps if s.status == "completed"])
        failed = len([s for s in steps if s.status == "failed"])
        return (
            f"Multi-step execution for '{request.objective}' finished. "
            f"Completed steps: {completed}. Failed steps: {failed}. "
            f"Overall status: {'completed' if ok else 'partial_failure'}."
        )
