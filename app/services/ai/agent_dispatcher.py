from __future__ import annotations

from typing import List

from app.models.ai_router import ProviderName, TaskType
from app.services.identity_core import get_system_prompt


class AgentDispatcher:
    """
    Maps task type -> agent name -> provider priority chain.
    """

    def get_agent_name(self, task_type: TaskType) -> str:
        if task_type == TaskType.CODING:
            return "coding_agent"
        if task_type == TaskType.RESEARCH:
            return "research_agent"
        if task_type == TaskType.REASONING:
            return "reasoning_agent"
        return "general_agent"

    def get_provider_chain(self, task_type: TaskType, preferred_provider: ProviderName | None) -> List[ProviderName]:
        if preferred_provider:
            base_chain = [preferred_provider]
        elif task_type == TaskType.CODING:
            base_chain = [
                ProviderName.OPENAI,
                ProviderName.ANTHROPIC,
                ProviderName.OLLAMA,
            ]
        elif task_type == TaskType.RESEARCH:
            base_chain = [
                ProviderName.OPENAI,
                ProviderName.ANTHROPIC,
                ProviderName.OLLAMA,
            ]
        elif task_type == TaskType.REASONING:
            base_chain = [
                ProviderName.ANTHROPIC,
                ProviderName.OPENAI,
                ProviderName.OLLAMA,
            ]
        else:
            base_chain = [
                ProviderName.OLLAMA,
                ProviderName.OPENAI,
                ProviderName.ANTHROPIC,
            ]

        unique_chain: List[ProviderName] = []
        seen = set()
        for provider in base_chain:
            if provider not in seen:
                seen.add(provider)
                unique_chain.append(provider)
        return unique_chain

    def build_system_prompt(self, task_type: TaskType) -> str:
        role_map = {
            TaskType.GENERAL: "agent",
            TaskType.REASONING: "reasoner",
            TaskType.CODING: "coder",
            TaskType.RESEARCH: "researcher",
        }
        return get_system_prompt(role_map[task_type], lang="en")
