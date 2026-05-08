from __future__ import annotations

from app.models.ai_router import TaskType
from app.models.specialized_agents import SpecializedExecutionRequest


class SpecializedPromptBuilder:
    def build(self, request: SpecializedExecutionRequest) -> str:
        if request.task_type == TaskType.CODING:
            return self._build_coding_prompt(request)
        if request.task_type == TaskType.RESEARCH:
            return self._build_research_prompt(request)
        if request.task_type == TaskType.REASONING:
            return self._build_reasoning_prompt(request)
        return self._build_general_prompt(request)

    def _build_coding_prompt(self, request: SpecializedExecutionRequest) -> str:
        return (
            f"Mission objective:\n{request.objective or '(not provided)'}\n\n"
            f"Task title:\n{request.title}\n\n"
            f"Task description:\n{request.description or '(empty)'}\n\n"
            f"Metadata:\n{request.metadata or {}}\n\n"
            "Respond in a concise implementation-focused format.\n"
            "If code is appropriate, return:\n"
            "1. Short explanation\n"
            "2. Directly usable code\n"
            "3. Short usage note\n"
            "Keep the answer compact and practical.\n"
            "Do not over-explain."
        )

    def _build_research_prompt(self, request: SpecializedExecutionRequest) -> str:
        return (
            f"Mission objective:\n{request.objective or '(not provided)'}\n\n"
            f"Task title:\n{request.title}\n\n"
            f"Task description:\n{request.description or '(empty)'}\n\n"
            f"Metadata:\n{request.metadata or {}}\n\n"
            "Provide a concise research-style summary.\n"
            "Return 3 to 5 actionable findings.\n"
            "Focus on useful practical recommendations, not fluff."
        )

    def _build_reasoning_prompt(self, request: SpecializedExecutionRequest) -> str:
        return (
            f"Mission objective:\n{request.objective or '(not provided)'}\n\n"
            f"Task title:\n{request.title}\n\n"
            f"Task description:\n{request.description or '(empty)'}\n\n"
            f"Metadata:\n{request.metadata or {}}\n\n"
            "Provide structured analysis, key tradeoffs, and a recommended next step.\n"
            "Keep the answer practical and architecture-aware."
        )

    def _build_general_prompt(self, request: SpecializedExecutionRequest) -> str:
        return (
            f"Mission objective:\n{request.objective or '(not provided)'}\n\n"
            f"Task title:\n{request.title}\n\n"
            f"Task description:\n{request.description or '(empty)'}\n\n"
            f"Metadata:\n{request.metadata or {}}\n\n"
            "Provide a concise, practical, execution-oriented answer."
        )
