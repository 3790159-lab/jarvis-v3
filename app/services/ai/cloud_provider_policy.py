from __future__ import annotations

import os
from typing import Dict, List

from app.models.ai_router import ProviderName, TaskType


class CloudProviderPolicy:
    def __init__(self) -> None:
        self.enable_openai = os.getenv("AI_ROUTER_ENABLE_OPENAI", "true").strip().lower() == "true"
        self.enable_anthropic = os.getenv("AI_ROUTER_ENABLE_ANTHROPIC", "true").strip().lower() == "true"
        self.enable_ollama = os.getenv("AI_ROUTER_ENABLE_OLLAMA", "true").strip().lower() == "true"

        self.has_openai_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
        self.has_anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

        self.default_mode = os.getenv("JARVIS_EXECUTION_MODE", "hybrid").strip().lower()
        self.external_executor_enabled = os.getenv("JARVIS_ENABLE_EXTERNAL_EXECUTOR", "true").strip().lower() == "true"

    def get_provider_chain(self, task_type: TaskType, preferred: ProviderName | None) -> List[str]:
        if preferred:
            return [preferred.value]

        if task_type == TaskType.CODING:
            return ["openai", "anthropic", "ollama"]
        if task_type == TaskType.REASONING:
            return ["anthropic", "openai", "ollama"]
        if task_type == TaskType.RESEARCH:
            return ["anthropic", "openai", "ollama"]
        return ["ollama", "openai", "anthropic"]

    def get_provider_status(self) -> List[Dict]:
        return [
            {
                "provider": "openai",
                "enabled": self.enable_openai,
                "configured": self.has_openai_key,
                "recommended_for": ["coding", "tool_use", "structured_tasks"],
            },
            {
                "provider": "anthropic",
                "enabled": self.enable_anthropic,
                "configured": self.has_anthropic_key,
                "recommended_for": ["reasoning", "long_context", "research"],
            },
            {
                "provider": "ollama",
                "enabled": self.enable_ollama,
                "configured": True,
                "recommended_for": ["local_fallback", "private_mode", "always_on"],
            },
        ]

    def resolve_execution_mode(self, task_type: TaskType) -> str:
        if self.default_mode in ("cloud", "local", "hybrid"):
            return self.default_mode
        if task_type == TaskType.CODING:
            return "hybrid"
        return "local"
