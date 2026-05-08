from __future__ import annotations

import os
from typing import List

from app.models.ai_router import ProviderName, TaskType
from app.models.provider_routing_policy import (
    ProviderRoutingDecision,
    ProviderRoutingHealthResponse,
    ProviderRoutingRequest,
)


class ProviderRoutingPolicyService:
    def __init__(self) -> None:
        pass

    def _read_state(self) -> dict:
        execution_mode = os.getenv("JARVIS_EXECUTION_MODE", "hybrid").strip().lower()
        routing_profile = os.getenv("JARVIS_ROUTING_PROFILE", "balanced").strip().lower()

        enable_openai = os.getenv("AI_ROUTER_ENABLE_OPENAI", "true").strip().lower() == "true"
        enable_anthropic = os.getenv("AI_ROUTER_ENABLE_ANTHROPIC", "true").strip().lower() == "true"
        enable_ollama = os.getenv("AI_ROUTER_ENABLE_OLLAMA", "true").strip().lower() == "true"

        has_openai = bool(os.getenv("OPENAI_API_KEY", "").strip())
        has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

        return {
            "execution_mode": execution_mode,
            "routing_profile": routing_profile,
            "enable_openai": enable_openai,
            "enable_anthropic": enable_anthropic,
            "enable_ollama": enable_ollama,
            "has_openai": has_openai,
            "has_anthropic": has_anthropic,
        }

    def health(self) -> ProviderRoutingHealthResponse:
        state = self._read_state()

        provider_status = {
            "openai": {
                "enabled": state["enable_openai"],
                "configured": state["has_openai"],
            },
            "anthropic": {
                "enabled": state["enable_anthropic"],
                "configured": state["has_anthropic"],
            },
            "ollama": {
                "enabled": state["enable_ollama"],
                "configured": True,
            },
        }

        available = self._available_providers(state)

        return ProviderRoutingHealthResponse(
            status="healthy",
            execution_mode=state["execution_mode"],
            routing_profile=state["routing_profile"],
            available_providers=available,
            provider_status=provider_status,
        )

    def decide(self, request: ProviderRoutingRequest) -> ProviderRoutingDecision:
        state = self._read_state()
        reasons: List[str] = []
        warnings: List[str] = []

        available = self._available_providers(state)

        if not available:
            return ProviderRoutingDecision(
                ok=False,
                task_type=request.task_type,
                preferred_provider=request.preferred_provider,
                resolved_mode=state["execution_mode"],
                resolved_profile=state["routing_profile"],
                selected_provider=None,
                provider_chain=[],
                reasons=[],
                warnings=["No providers are currently available"],
                available_providers=[],
                error="No providers are currently available",
            )

        mode = self._normalize_mode(state["execution_mode"], available, warnings)

        chain = self._build_chain(
            task_type=request.task_type,
            preferred=request.preferred_provider,
            mode=mode,
            profile=state["routing_profile"],
            available=available,
            reasons=reasons,
            warnings=warnings,
        )

        chain = self._filter_available(chain, available)

        if not chain:
            return ProviderRoutingDecision(
                ok=False,
                task_type=request.task_type,
                preferred_provider=request.preferred_provider,
                resolved_mode=mode,
                resolved_profile=state["routing_profile"],
                selected_provider=None,
                provider_chain=[],
                reasons=reasons,
                warnings=warnings + ["Resolved provider chain is empty after availability filtering"],
                available_providers=available,
                error="Resolved provider chain is empty after availability filtering",
            )

        return ProviderRoutingDecision(
            ok=True,
            task_type=request.task_type,
            preferred_provider=request.preferred_provider,
            resolved_mode=mode,
            resolved_profile=state["routing_profile"],
            selected_provider=chain[0],
            provider_chain=chain,
            reasons=reasons,
            warnings=warnings,
            available_providers=available,
            error=None,
        )

    def _available_providers(self, state: dict) -> List[ProviderName]:
        result: List[ProviderName] = []

        if state["enable_openai"] and state["has_openai"]:
            result.append(ProviderName.OPENAI)

        if state["enable_anthropic"] and state["has_anthropic"]:
            result.append(ProviderName.ANTHROPIC)

        if state["enable_ollama"]:
            result.append(ProviderName.OLLAMA)

        return result

    def _normalize_mode(self, mode: str, available: List[ProviderName], warnings: List[str]) -> str:
        mode = (mode or "hybrid").lower()

        if mode not in ("local", "cloud", "hybrid", "cost_saver"):
            warnings.append(f"Unknown execution mode '{mode}', fallback to hybrid")
            mode = "hybrid"

        has_cloud = any(p in available for p in (ProviderName.OPENAI, ProviderName.ANTHROPIC))
        has_local = ProviderName.OLLAMA in available

        if mode == "cloud" and not has_cloud:
            warnings.append("Cloud mode requested but no cloud providers are configured; fallback applied")
            return "local" if has_local else "hybrid"

        if mode == "local" and not has_local:
            warnings.append("Local mode requested but Ollama unavailable; fallback applied")
            return "cloud" if has_cloud else "hybrid"

        return mode

    def _build_chain(
        self,
        task_type: TaskType,
        preferred: ProviderName | None,
        mode: str,
        profile: str,
        available: List[ProviderName],
        reasons: List[str],
        warnings: List[str],
    ) -> List[ProviderName]:
        if preferred:
            if preferred in available:
                reasons.append(f"Preferred provider '{preferred.value}' is available and selected first")
                return [preferred] + [p for p in available if p != preferred]
            warnings.append(f"Preferred provider '{preferred.value}' is unavailable; policy routing used instead")

        if mode == "local":
            reasons.append("Execution mode is local; Ollama is prioritized")
            return [ProviderName.OLLAMA, ProviderName.OPENAI, ProviderName.ANTHROPIC]

        if mode == "cloud":
            reasons.append("Execution mode is cloud; cloud providers are prioritized")
            if task_type == TaskType.CODING:
                return [ProviderName.OPENAI, ProviderName.ANTHROPIC, ProviderName.OLLAMA]
            if task_type in (TaskType.REASONING, TaskType.RESEARCH):
                return [ProviderName.ANTHROPIC, ProviderName.OPENAI, ProviderName.OLLAMA]
            return [ProviderName.OPENAI, ProviderName.ANTHROPIC, ProviderName.OLLAMA]

        if mode == "cost_saver":
            reasons.append("Execution mode is cost_saver; local provider is preferred")
            if task_type == TaskType.CODING:
                return [ProviderName.OLLAMA, ProviderName.OPENAI, ProviderName.ANTHROPIC]
            if task_type in (TaskType.REASONING, TaskType.RESEARCH):
                return [ProviderName.OLLAMA, ProviderName.ANTHROPIC, ProviderName.OPENAI]
            return [ProviderName.OLLAMA, ProviderName.OPENAI, ProviderName.ANTHROPIC]

        if profile == "quality_first":
            reasons.append("Hybrid mode with quality_first profile")
            if task_type == TaskType.CODING:
                return [ProviderName.OPENAI, ProviderName.ANTHROPIC, ProviderName.OLLAMA]
            if task_type in (TaskType.REASONING, TaskType.RESEARCH):
                return [ProviderName.ANTHROPIC, ProviderName.OPENAI, ProviderName.OLLAMA]
            return [ProviderName.OPENAI, ProviderName.ANTHROPIC, ProviderName.OLLAMA]

        if profile == "local_first":
            reasons.append("Hybrid mode with local_first profile")
            if task_type == TaskType.CODING:
                return [ProviderName.OLLAMA, ProviderName.OPENAI, ProviderName.ANTHROPIC]
            if task_type in (TaskType.REASONING, TaskType.RESEARCH):
                return [ProviderName.OLLAMA, ProviderName.ANTHROPIC, ProviderName.OPENAI]
            return [ProviderName.OLLAMA, ProviderName.OPENAI, ProviderName.ANTHROPIC]

        reasons.append("Hybrid mode with balanced profile")
        if task_type == TaskType.CODING:
            return [ProviderName.OPENAI, ProviderName.OLLAMA, ProviderName.ANTHROPIC]
        if task_type == TaskType.REASONING:
            return [ProviderName.ANTHROPIC, ProviderName.OPENAI, ProviderName.OLLAMA]
        if task_type == TaskType.RESEARCH:
            return [ProviderName.ANTHROPIC, ProviderName.OPENAI, ProviderName.OLLAMA]
        return [ProviderName.OLLAMA, ProviderName.OPENAI, ProviderName.ANTHROPIC]

    @staticmethod
    def _filter_available(chain: List[ProviderName], available: List[ProviderName]) -> List[ProviderName]:
        return [item for item in chain if item in available]
