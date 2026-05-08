from __future__ import annotations

from pydantic import BaseModel

from .models import TaskRuntimeState


class RecoveryPlan(BaseModel):
    action: str
    reason: str
    retryable: bool = True
    fallback_provider: str = ""
    consult_capability: str = ""
    escalation_level: int = 0
    requires_approval: bool = False


class RecoveryGuardian:
    def plan(self, task_state: TaskRuntimeState, error_text: str) -> RecoveryPlan:
        text = (error_text or "").lower()

        if "timeout" in text:
            return RecoveryPlan(
                action="retry_other_provider",
                reason="timeout_detected",
                retryable=True,
                fallback_provider="openai_compatible",
                escalation_level=1,
            )

        if "empty" in text or "invalid" in text:
            return RecoveryPlan(
                action="fallback_provider_and_validate",
                reason="empty_or_invalid_output",
                retryable=True,
                fallback_provider="ollama",
                consult_capability="validate",
                escalation_level=1,
            )

        if "policy" in text or "approval" in text:
            return RecoveryPlan(
                action="pause_for_approval",
                reason="policy_or_approval_required",
                retryable=False,
                escalation_level=2,
                requires_approval=True,
            )

        if task_state.attempts >= task_state.task.max_retries:
            return RecoveryPlan(
                action="escalate_and_fail",
                reason="max_retries_exhausted",
                retryable=False,
                escalation_level=3,
                requires_approval=True,
            )

        return RecoveryPlan(
            action="retry",
            reason="generic_runtime_error",
            retryable=True,
            fallback_provider=task_state.task.metadata.get("fallback_provider", ""),
            escalation_level=1,
        )