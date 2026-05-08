from __future__ import annotations

from typing import Dict

from app.models.ai_router import DispatchRequest
from app.models.external_executor import (
    CloudExecutorHealthResponse,
    CloudProviderStatus,
    ExternalExecutorRequest,
    ExternalExecutorResponse,
)
from app.models.provider_routing_policy import ProviderRoutingRequest
from app.services.ai.ai_router_service import AIRouterService
from app.services.ai.provider_routing_policy_service import ProviderRoutingPolicyService


class ExternalExecutorService:
    def __init__(self) -> None:
        self.router = AIRouterService()

    def _policy(self) -> ProviderRoutingPolicyService:
        return ProviderRoutingPolicyService()

    def health(self) -> CloudExecutorHealthResponse:
        health = self._policy().health()

        providers = []
        for provider_name, state in health.provider_status.items():
            providers.append(
                CloudProviderStatus(
                    provider=provider_name,
                    enabled=state.get("enabled", False),
                    configured=state.get("configured", False),
                    recommended_for=[],
                )
            )

        return CloudExecutorHealthResponse(
            status=health.status,
            providers=providers,
            external_executor_enabled=True,
            default_mode=health.execution_mode,
        )

    async def execute(self, request: ExternalExecutorRequest) -> ExternalExecutorResponse:
        decision = self._policy().decide(
            ProviderRoutingRequest(
                task_type=request.task_type,
                preferred_provider=request.preferred_provider,
                metadata=request.metadata,
            )
        )

        execution_plan: Dict[str, object] = {
            "title": request.title,
            "objective": request.objective,
            "task_type": request.task_type.value,
            "mode": decision.resolved_mode,
            "routing_profile": decision.resolved_profile,
            "selected_provider": decision.selected_provider.value if decision.selected_provider else "none",
            "provider_chain": [p.value for p in decision.provider_chain],
            "reasons": decision.reasons,
            "warnings": decision.warnings,
            "metadata": request.metadata,
        }

        if not decision.ok:
            return ExternalExecutorResponse(
                ok=False,
                mode=decision.resolved_mode,
                selected_provider="none",
                selected_executor="external_executor",
                task_type=request.task_type,
                dry_run=request.dry_run,
                execution_plan=execution_plan,
                response_text="",
                error=decision.error or "Routing decision failed",
            )

        if request.dry_run:
            return ExternalExecutorResponse(
                ok=True,
                mode=decision.resolved_mode,
                selected_provider=decision.selected_provider.value,
                selected_executor="external_executor_dry_run",
                task_type=request.task_type,
                dry_run=True,
                execution_plan=execution_plan,
                response_text="Dry run completed using STRICT policy routing.",
                error=None,
            )

        dispatch = await self.router.dispatch(
            DispatchRequest(
                prompt=request.description or request.title,
                task_type=request.task_type,
                preferred_provider=decision.selected_provider,
                metadata={
                    "force_provider": decision.selected_provider.value,
                    "external_executor": True,
                    "routing_profile": decision.resolved_profile,
                },
            )
        )

        return ExternalExecutorResponse(
            ok=dispatch.ok,
            mode=decision.resolved_mode,
            selected_provider=decision.selected_provider.value,
            selected_executor=dispatch.selected_agent,
            task_type=request.task_type,
            dry_run=False,
            execution_plan=execution_plan,
            response_text=dispatch.response_text,
            error=dispatch.error,
        )
