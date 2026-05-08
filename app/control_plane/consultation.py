from __future__ import annotations

import uuid
from collections import defaultdict

from .models import ConsultationKind, ConsultRequest, ConsultResponse, TaskSpec
from .registry import AgentRegistry


class ConsultationCoordinator:
    def __init__(self, registry: AgentRegistry, max_depth_default: int = 2, max_requests_per_task: int = 3) -> None:
        self.registry = registry
        self.max_depth_default = max_depth_default
        self.max_requests_per_task = max_requests_per_task
        self.task_counts: dict[str, int] = defaultdict(int)

    def build_request(
        self,
        task: TaskSpec,
        from_agent_id: str,
        requested_capability: str,
        reason: str,
        kind: ConsultationKind = ConsultationKind.CONSULT,
        expected_output: str = "",
    ) -> ConsultRequest:
        return ConsultRequest(
            request_id=f"consult_{uuid.uuid4().hex[:10]}",
            task_id=task.task_id,
            from_agent_id=from_agent_id,
            kind=kind,
            requested_capability=requested_capability,
            reason=reason,
            expected_output=expected_output,
            max_depth=task.max_consults or self.max_depth_default,
        )

    def resolve(self, request: ConsultRequest) -> ConsultResponse | None:
        if self.task_counts[request.task_id] >= self.max_requests_per_task:
            return None

        helper = self.registry.choose_agent(
            request.requested_capability,
            exclude={request.from_agent_id},
        )
        if not helper:
            return None

        self.task_counts[request.task_id] += 1
        return ConsultResponse(
            request_id=request.request_id,
            helper_agent_id=helper.agent_id,
            summary=(
                f"{helper.agent_id} recommends focusing on "
                f"capability '{request.requested_capability}' for: {request.reason}"
            ),
            recommended_actions=[
                "Review dependencies before execution.",
                "Return normalized output and validation hints.",
            ],
            confidence=0.74,
        )