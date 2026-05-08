from __future__ import annotations

from typing import List

from app.models.ai_router import TaskType
from app.models.mission_ai import (
    MissionAIRequest,
    MissionAIResponse,
    MissionAITask,
    MissionAITaskExecutionResult,
    TaskClassificationResult,
)
from app.models.specialized_agents import SpecializedExecutionRequest
from app.services.ai.specialized_agent_service import SpecializedAgentService
from app.services.ai.task_classifier import TaskClassifier


class MissionAIOrchestrator:
    def __init__(self) -> None:
        self.classifier = TaskClassifier()
        self.specialized_agents = SpecializedAgentService()

    def classify_task(self, task: MissionAITask, objective: str = "") -> TaskClassificationResult:
        return self.classifier.classify(task=task, objective=objective)

    async def execute(self, request: MissionAIRequest) -> MissionAIResponse:
        tasks = request.tasks or [
            MissionAITask(
                task_id="task_001",
                title=request.objective,
                description="Auto-generated task from mission objective",
                metadata={},
            )
        ]

        results: List[MissionAITaskExecutionResult] = []
        completed = 0
        failed = 0

        for task in tasks:
            classification = self.classifier.classify(task=task, objective=request.objective)

            execution_result = await self._execute_with_fallback(
                request=request,
                task=task,
                classification=classification,
            )

            results.append(execution_result)

            if execution_result.ok:
                completed += 1
            else:
                failed += 1
                if request.stop_on_error:
                    break

        return MissionAIResponse(
            ok=(failed == 0),
            mission_id=request.mission_id,
            objective=request.objective,
            total_tasks=len(tasks),
            completed_tasks=completed,
            failed_tasks=failed,
            results=results,
        )

    async def _execute_with_fallback(
        self,
        request: MissionAIRequest,
        task: MissionAITask,
        classification: TaskClassificationResult,
    ) -> MissionAITaskExecutionResult:
        task_type_chain = self._build_task_type_chain(classification.detected_task_type)

        collected_errors: List[str] = []

        for current_task_type in task_type_chain:
            specialized_request = SpecializedExecutionRequest(
                title=task.title,
                description=task.description,
                objective=request.objective,
                task_type=current_task_type,
                preferred_provider=request.preferred_provider,
                metadata={
                    "mission_id": request.mission_id,
                    "task_id": task.task_id,
                    "task_title": task.title,
                    "original_detected_task_type": classification.detected_task_type.value,
                    "effective_task_type": current_task_type.value,
                    "classification_confidence": classification.confidence,
                    "classification_reasons": classification.reasons,
                    **(task.metadata or {}),
                    **(request.mission_metadata or {}),
                },
            )

            dispatch_result = await self.specialized_agents.execute(specialized_request)

            if dispatch_result.ok:
                return MissionAITaskExecutionResult(
                    task_id=task.task_id,
                    title=task.title,
                    detected_task_type=current_task_type,
                    selected_agent=dispatch_result.selected_agent,
                    selected_provider=dispatch_result.selected_provider,
                    attempted_providers=dispatch_result.attempted_providers,
                    ok=True,
                    response_text=dispatch_result.response_text,
                    error=None,
                )

            collected_errors.append(
                f"{current_task_type.value}: {dispatch_result.error or 'unknown error'}"
            )

        return MissionAITaskExecutionResult(
            task_id=task.task_id,
            title=task.title,
            detected_task_type=classification.detected_task_type,
            selected_agent="fallback_failed",
            selected_provider=(request.preferred_provider or self.specialized_agents.router.default_provider),
            attempted_providers=[],
            ok=False,
            response_text="",
            error=" | ".join(collected_errors) if collected_errors else "No available provider succeeded",
        )

    @staticmethod
    def _build_task_type_chain(initial_task_type: TaskType) -> List[TaskType]:
        if initial_task_type == TaskType.RESEARCH:
            return [TaskType.RESEARCH, TaskType.REASONING, TaskType.GENERAL]
        if initial_task_type == TaskType.CODING:
            return [TaskType.CODING, TaskType.REASONING]
        if initial_task_type == TaskType.REASONING:
            return [TaskType.REASONING, TaskType.GENERAL]
        return [TaskType.GENERAL]

