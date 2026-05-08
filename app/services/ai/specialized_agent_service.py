from __future__ import annotations

from typing import List

from app.models.ai_router import DispatchRequest, TaskType
from app.models.specialized_agents import (
    SpecializedExecutionRequest,
    SpecializedExecutionResult,
)
from app.services.ai.ai_router_service import AIRouterService
from app.services.ai.specialized_prompt_builder import SpecializedPromptBuilder


class SpecializedAgentService:
    def __init__(self) -> None:
        self.router = AIRouterService()
        self.prompt_builder = SpecializedPromptBuilder()

    async def execute(self, request: SpecializedExecutionRequest) -> SpecializedExecutionResult:
        task_type_chain = self._build_task_type_chain(request.task_type)
        collected_errors: List[str] = []
        prompt_used = ""

        for effective_task_type in task_type_chain:
            effective_request = SpecializedExecutionRequest(
                title=request.title,
                description=request.description,
                objective=request.objective,
                task_type=effective_task_type,
                preferred_provider=request.preferred_provider,
                metadata=request.metadata,
            )

            prompt = self.prompt_builder.build(effective_request)
            prompt_used = prompt

            dispatch_request = DispatchRequest(
                prompt=prompt,
                task_type=effective_task_type,
                preferred_provider=request.preferred_provider,
                metadata={
                    "specialized_agent_mode": True,
                    "title": request.title,
                    "objective": request.objective,
                    "original_task_type": request.task_type.value,
                    "effective_task_type": effective_task_type.value,
                    **(request.metadata or {}),
                },
            )

            dispatch_result = await self.router.dispatch(dispatch_request)

            if dispatch_result.ok:
                return SpecializedExecutionResult(
                    ok=True,
                    task_type=effective_task_type,
                    selected_agent=dispatch_result.selected_agent,
                    selected_provider=dispatch_result.selected_provider,
                    attempted_providers=dispatch_result.attempted_providers,
                    response_text=dispatch_result.response_text,
                    prompt_used=prompt,
                    error=None,
                )

            collected_errors.append(
                f"{effective_task_type.value}: {dispatch_result.error or 'unknown error'}"
            )

        return SpecializedExecutionResult(
            ok=False,
            task_type=request.task_type,
            selected_agent=f"{request.task_type.value}_agent",
            selected_provider=request.preferred_provider or self.router.default_provider,
            attempted_providers=[],
            response_text="",
            prompt_used=prompt_used,
            error=" | ".join(collected_errors) if collected_errors else "No available provider succeeded",
        )

    def _build_task_type_chain(self, task_type: TaskType) -> List[TaskType]:
        if task_type == TaskType.CODING:
            return [TaskType.CODING, TaskType.REASONING]
        if task_type == TaskType.RESEARCH:
            return [TaskType.RESEARCH, TaskType.REASONING, TaskType.GENERAL]
        if task_type == TaskType.REASONING:
            return [TaskType.REASONING, TaskType.GENERAL]
        return [TaskType.GENERAL]

    async def execute_coding(
        self,
        title: str,
        description: str = "",
        objective: str = "",
        metadata: dict | None = None,
    ) -> SpecializedExecutionResult:
        request = SpecializedExecutionRequest(
            title=title,
            description=description,
            objective=objective,
            task_type=TaskType.CODING,
            metadata=metadata or {},
        )
        return await self.execute(request)

    async def execute_research(
        self,
        title: str,
        description: str = "",
        objective: str = "",
        metadata: dict | None = None,
    ) -> SpecializedExecutionResult:
        request = SpecializedExecutionRequest(
            title=title,
            description=description,
            objective=objective,
            task_type=TaskType.RESEARCH,
            metadata=metadata or {},
        )
        return await self.execute(request)

    async def execute_reasoning(
        self,
        title: str,
        description: str = "",
        objective: str = "",
        metadata: dict | None = None,
    ) -> SpecializedExecutionResult:
        request = SpecializedExecutionRequest(
            title=title,
            description=description,
            objective=objective,
            task_type=TaskType.REASONING,
            metadata=metadata or {},
        )
        return await self.execute(request)
