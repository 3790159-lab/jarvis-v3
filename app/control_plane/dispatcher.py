from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field

from .applied_learning import AppliedLearningEngine
from .communication_policy import CommunicationPolicyStore
from .consultation import ConsultationCoordinator
from .models import ConsultationKind, ConsultRequest, MissionSnapshot, TaskSpec
from .provider_router import ProviderRouter
from .registry import AgentRegistry
from .strategy_runtime import StrategyRuntimeStore


class DispatchDecision(BaseModel):
    task_id: str
    agent_id: str
    provider: str
    provider_reason: str
    fallback_chain: list[str] = Field(default_factory=list)
    consultations: list[ConsultRequest] = Field(default_factory=list)
    learning_guidance: dict = Field(default_factory=dict)


class Dispatcher:
    def __init__(
        self,
        registry: AgentRegistry,
        provider_router: ProviderRouter,
        consultation_coordinator: ConsultationCoordinator,
        applied_learning: AppliedLearningEngine | None = None,
        strategy_path: str | Path = "state/agent_mesh/strategy_runtime.json",
        communication_policy_path: str | Path = "state/agent_mesh/communication_policy.json",
    ) -> None:
        self.registry = registry
        self.provider_router = provider_router
        self.consultation_coordinator = consultation_coordinator
        self.applied_learning = applied_learning
        self.strategy = StrategyRuntimeStore(Path(strategy_path))
        self.comm_policy = CommunicationPolicyStore(Path(communication_policy_path))

    def plan(
        self,
        task: TaskSpec,
        snapshot: MissionSnapshot,
        context: dict,
        exclude_agents: set[str] | None = None,
    ) -> DispatchDecision:
        agent = self.registry.choose_agent(
            task.required_capability,
            exclude=exclude_agents or set(),
            preferred_agent_id=task.preferred_agent_id,
        )
        if not agent:
            raise RuntimeError(f"No agent found for capability: {task.required_capability}")

        learning_guidance = {}
        if self.applied_learning is not None:
            guidance = self.applied_learning.apply(task, agent.agent_id)
            learning_guidance = guidance.model_dump(mode="json")

        preferred_provider = str(task.metadata.get("forced_provider") or agent.preferred_provider)

        route = self.provider_router.route(
            task=task,
            preferred_provider=preferred_provider,
            internet_available=context.get("internet_available", True),
            provider_health=context.get("provider_health", {}),
            locality_required=task.locality_requirement,
            storage_affinity=task.storage_affinity,
        )

        strategy_bonus = int(self.strategy.get_knob("consultation_bonus", 0) or 0)
        allowed_helpers = set(self.comm_policy.allowed_helpers(task.required_capability))
        max_consults = self.comm_policy.max_consults(task.task_type, task.risk_level, strategy_bonus=strategy_bonus)

        consultations: list[ConsultRequest] = []

        def maybe_add(requested_capability: str, reason: str, kind: str, expected_output: str):
            if len(consultations) >= max_consults:
                return
            if requested_capability not in allowed_helpers:
                return
            consultations.append(
                self.consultation_coordinator.build_request(
                    task=task,
                    from_agent_id=agent.agent_id,
                    requested_capability=requested_capability,
                    reason=reason,
                    kind=kind,
                    expected_output=expected_output,
                )
            )

        if task.consultation_allowed and task.risk_level in {"high", "critical"} and task.required_capability != "validate":
            maybe_add(
                requested_capability="validate",
                reason="High-risk task requires QA second opinion before final commit.",
                kind=ConsultationKind.SECOND_OPINION,
                expected_output="Validation hints and risk notes.",
            )

        if task.metadata.get("needs_research"):
            maybe_add(
                requested_capability="analyze",
                reason="Task requests additional context support.",
                kind=ConsultationKind.SUPPORT,
                expected_output="Relevant context bundle or research summary.",
            )

        if task.metadata.get("needs_memory_support") and task.task_type != "integration":
            maybe_add(
                requested_capability="context_bundle",
                reason="Task requests memory support.",
                kind=ConsultationKind.SUPPORT,
                expected_output="Mission memory context bundle.",
            )

        return DispatchDecision(
            task_id=task.task_id,
            agent_id=agent.agent_id,
            provider=route.provider,
            provider_reason=route.reason,
            fallback_chain=route.fallback_chain,
            consultations=consultations[:max_consults],
            learning_guidance=learning_guidance,
        )