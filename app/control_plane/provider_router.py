from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from pydantic import BaseModel, Field

from .adaptive_policy import AdaptivePolicyStore
from .models import TaskSpec


class ProviderRouteDecision(BaseModel):
    provider: str
    reason: str
    fallback_chain: list[str] = Field(default_factory=list)


class ProviderRouter:
    def __init__(self, policy_path: str | Path = "state/agent_mesh/adaptive_policy.json") -> None:
        self.policy = AdaptivePolicyStore(Path(policy_path))

    def route(
        self,
        task: TaskSpec,
        preferred_provider: Optional[str] = None,
        internet_available: bool = True,
        provider_health: Optional[Dict[str, str]] = None,
        locality_required: bool = False,
        storage_affinity: bool = False,
    ) -> ProviderRouteDecision:
        health = provider_health or {}
        adaptive = self.policy.get_provider_preference(task.required_capability)

        def is_healthy(name: str) -> bool:
            return health.get(name, "healthy") == "healthy"

        if not internet_available:
            return ProviderRouteDecision(
                provider="ollama",
                reason="internet_unavailable",
                fallback_chain=["ollama"],
            )

        if locality_required or storage_affinity or task.locality_requirement or task.storage_affinity:
            return ProviderRouteDecision(
                provider="ollama",
                reason="locality_or_storage_affinity",
                fallback_chain=["ollama", "cloud"],
            )

        if adaptive:
            if adaptive == "cloud" and is_healthy("cloud"):
                return ProviderRouteDecision(
                    provider="cloud",
                    reason=f"adaptive_preference:{task.required_capability}=cloud",
                    fallback_chain=["openai_compatible", "ollama"],
                )
            if adaptive == "openai_compatible" and is_healthy("openai_compatible"):
                return ProviderRouteDecision(
                    provider="openai_compatible",
                    reason=f"adaptive_preference:{task.required_capability}=openai_compatible",
                    fallback_chain=["cloud", "ollama"],
                )
            if adaptive == "ollama":
                return ProviderRouteDecision(
                    provider="ollama",
                    reason=f"adaptive_preference:{task.required_capability}=ollama",
                    fallback_chain=["cloud"],
                )

        if preferred_provider == "cloud" and is_healthy("cloud"):
            return ProviderRouteDecision(
                provider="cloud",
                reason="preferred_cloud_available",
                fallback_chain=["openai_compatible", "ollama"],
            )

        if preferred_provider == "ollama":
            return ProviderRouteDecision(
                provider="ollama",
                reason="preferred_ollama",
                fallback_chain=["cloud"],
            )

        if is_healthy("cloud"):
            return ProviderRouteDecision(
                provider="cloud",
                reason="cloud_primary_available",
                fallback_chain=["openai_compatible", "ollama"],
            )

        if is_healthy("openai_compatible"):
            return ProviderRouteDecision(
                provider="openai_compatible",
                reason="cloud_degraded_using_online_alternative",
                fallback_chain=["ollama"],
            )

        return ProviderRouteDecision(
            provider="ollama",
            reason="all_online_providers_unavailable",
            fallback_chain=["ollama"],
        )