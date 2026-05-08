from __future__ import annotations

import os
from typing import List

from app.models.ai_router import (
    DispatchRequest,
    DispatchResult,
    ProviderHealth,
    ProviderName,
    RouterHealthResponse,
)
from app.services.ai.agent_dispatcher import AgentDispatcher
from app.services.ai.provider_anthropic import AnthropicProvider
from app.services.ai.provider_ollama import OllamaProvider
from app.services.ai.provider_openai import OpenAIProvider


class AIRouterService:
    def __init__(self) -> None:
        timeout_seconds = int(os.getenv("AI_ROUTER_TIMEOUT_SECONDS", "120"))

        self.providers = {
            ProviderName.OPENAI: OpenAIProvider(timeout_seconds=timeout_seconds),
            ProviderName.ANTHROPIC: AnthropicProvider(timeout_seconds=timeout_seconds),
            ProviderName.OLLAMA: OllamaProvider(timeout_seconds=timeout_seconds),
        }

        default_provider_raw = os.getenv("AI_ROUTER_DEFAULT_PROVIDER", "ollama").strip().lower()
        try:
            self.default_provider = ProviderName(default_provider_raw)
        except Exception:
            self.default_provider = ProviderName.OLLAMA

        self.dispatcher = AgentDispatcher()

    def health(self) -> RouterHealthResponse:
        providers: List[ProviderHealth] = []
        for provider_name, provider in self.providers.items():
            providers.append(
                ProviderHealth(
                    provider=provider_name,
                    enabled=provider.is_enabled(),
                    configured=provider.is_configured(),
                    details={
                        "timeout_seconds": provider.timeout_seconds,
                    },
                )
            )

        return RouterHealthResponse(
            status="healthy",
            providers=providers,
            default_provider=self.default_provider,
        )

    async def dispatch(self, request: DispatchRequest) -> DispatchResult:
        agent_name = self.dispatcher.get_agent_name(request.task_type)
        attempted: List[ProviderName] = []

        # ✅ STRICT preferred provider mode
        if request.preferred_provider is not None:
            provider_chain = [request.preferred_provider]
        else:
            provider_chain = self.dispatcher.get_provider_chain(
                task_type=request.task_type,
                preferred_provider=None,
            )

        if not provider_chain:
            provider_chain = [self.default_provider]

        final_system_prompt = request.system_prompt or self.dispatcher.build_system_prompt(request.task_type)
        last_error = None

        for provider_name in provider_chain:
            provider = self.providers.get(provider_name)

            attempted.append(provider_name)

            if provider is None:
                last_error = f"Provider {provider_name.value} is not registered"
                continue

            if not provider.is_enabled():
                last_error = f"Provider {provider_name.value} is disabled"
                continue

            if not provider.is_configured():
                last_error = f"Provider {provider_name.value} is not configured"
                continue

            try:
                result = await provider.generate(
                    prompt=request.prompt,
                    system_prompt=final_system_prompt,
                    metadata=request.metadata,
                )

                return DispatchResult(
                    ok=True,
                    task_type=request.task_type,
                    selected_agent=agent_name,
                    selected_provider=provider_name,
                    attempted_providers=attempted,
                    response_text=result.get("text", ""),
                    raw_response=result,
                    error=None,
                )
            except Exception as exc:
                last_error = str(exc)

        return DispatchResult(
            ok=False,
            task_type=request.task_type,
            selected_agent=agent_name,
            selected_provider=attempted[-1] if attempted else self.default_provider,
            attempted_providers=attempted,
            response_text="",
            raw_response={},
            error=last_error or "No available provider succeeded",
        )
