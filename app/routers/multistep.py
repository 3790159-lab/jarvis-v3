from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.mission_resume_store import (
    acquire_lock,
    create_or_update_snapshot,
    log_event,
    release_lock,
    touch_heartbeat,
)
from app.services.mission_result_packager import package_mission_result
from app.services.tool_chain_executor import execute_tool_chain
from app.services.tool_chain_planner import plan_tool_chain


router = APIRouter(tags=["multistep"])


class MultiStepItem(BaseModel):
    step_id: str
    title: str
    description: str
    task_type: str = "reasoning"
    preferred_provider: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MultiStepMissionRequest(BaseModel):
    mission_id: str
    objective: str
    steps: List[MultiStepItem] = Field(default_factory=list)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _provider_available(name: Optional[str], ai_health: Dict[str, Any]) -> bool:
    if not name:
        return False
    normalized = str(name).strip().lower()
    for provider in ai_health.get("providers", []) or []:
        if str(provider.get("provider", "")).strip().lower() == normalized:
            return bool(provider.get("enabled")) and bool(provider.get("configured"))
    return False


def _build_ai_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "providers": [
            {"provider": "openai", "enabled": True, "configured": False, "details": {"timeout_seconds": 600}},
            {"provider": "anthropic", "enabled": True, "configured": False, "details": {"timeout_seconds": 600}},
            {"provider": "ollama", "enabled": True, "configured": True, "details": {"timeout_seconds": 600}},
        ],
        "default_provider": "ollama",
    }


def _resolve_provider(preferred_provider: Optional[str], ai_health: Dict[str, Any]) -> str:
    requested = (preferred_provider or "").strip().lower()
    if _provider_available(requested, ai_health):
        return requested
    for candidate in ["ollama", "openai", "anthropic"]:
        if _provider_available(candidate, ai_health):
            return candidate
    return "ollama"


def _generate_response_text(objective: str, step: MultiStepItem, resolved_provider: str) -> str:
    phase = (step.metadata or {}).get("phase") or step.task_type

    if step.task_type == "reasoning":
        return f"[{resolved_provider}] {step.title}: Reasoning step completed for objective '{objective}'. Phase: {phase}. Summary: {step.description}"

    if step.task_type == "coding":
        return f"[{resolved_provider}] {step.title}: Implementation step completed. A safe fallback execution path was used for '{objective}'. Work item: {step.description}"

    return f"[{resolved_provider}] {step.title}: Step completed for objective '{objective}'. Details: {step.description}"


def _execute_planned_chain(objective: str, step: MultiStepItem) -> Dict[str, Any]:
    step_metadata = dict(step.metadata or {})
    chain_plan = plan_tool_chain(
        objective,
        {
            "step_id": step.step_id,
            "title": step.title,
            "description": step.description,
            "task_type": step.task_type,
            "preferred_provider": step.preferred_provider,
            "metadata": step_metadata,
        },
    )

    actions = list(chain_plan.get("actions") or [])
    fallback_chains = list(chain_plan.get("fallback_chains") or [])
    chain_attempts: List[Dict[str, Any]] = []

    if not actions:
        return {
            "tool": None,
            "tool_payload": {},
            "tool_result": None,
            "tool_chain_plan": [],
            "tool_chain_result": None,
            "execution_attempts": [],
            "chain_attempts": [],
            "routing_source": chain_plan.get("source", "none"),
            "routing_reason": chain_plan.get("reason", "No plan generated"),
            "routing_confidence": float(chain_plan.get("confidence") or 0.0),
            "routing_fallback_count": len(fallback_chains),
            "step_status": "failed",
            "step_error": "No tool chain actions were generated",
        }

    primary_result = execute_tool_chain(
        actions=actions,
        metadata=step_metadata,
        objective=objective,
        step_description=step.description,
    )
    chain_attempts.append(
        {
            "chain_source": chain_plan.get("source"),
            "chain_reason": chain_plan.get("reason"),
            "chain_confidence": chain_plan.get("confidence"),
            "actions": _json_safe(actions),
            "result": _json_safe(primary_result),
        }
    )

    final_chain = actions
    final_result = primary_result
    final_source = chain_plan.get("source")
    final_reason = chain_plan.get("reason")
    final_confidence = float(chain_plan.get("confidence") or 0.0)

    if not primary_result.get("ok", False):
        for fallback in fallback_chains:
            fallback_actions = list(fallback.get("actions") or [])
            if not fallback_actions:
                continue

            fallback_result = execute_tool_chain(
                actions=fallback_actions,
                metadata=step_metadata,
                objective=objective,
                step_description=step.description,
            )
            chain_attempts.append(
                {
                    "chain_source": fallback.get("source"),
                    "chain_reason": fallback.get("reason"),
                    "chain_confidence": fallback.get("confidence"),
                    "actions": _json_safe(fallback_actions),
                    "result": _json_safe(fallback_result),
                }
            )

            final_chain = fallback_actions
            final_result = fallback_result
            final_source = fallback.get("source")
            final_reason = fallback.get("reason")
            final_confidence = float(fallback.get("confidence") or 0.0)

            if fallback_result.get("ok", False):
                break

    first_action = final_chain[0] if final_chain else {}

    return {
        "tool": first_action.get("tool"),
        "tool_payload": _json_safe(first_action.get("payload") or {}),
        "tool_result": _json_safe(final_result.get("final_result")),
        "tool_chain_plan": _json_safe(final_chain),
        "tool_chain_result": _json_safe(final_result),
        "execution_attempts": _json_safe(final_result.get("execution_attempts") or []),
        "chain_attempts": _json_safe(chain_attempts),
        "routing_source": final_source,
        "routing_reason": final_reason,
        "routing_confidence": final_confidence,
        "routing_fallback_count": len(fallback_chains),
        "step_status": "completed" if final_result.get("ok", False) else "failed",
        "step_error": final_result.get("error"),
    }


def _execute_multistep(payload: MultiStepMissionRequest) -> Dict[str, Any]:
    if not acquire_lock(payload.mission_id):
        raise HTTPException(status_code=409, detail="Mission is already running or locked")

    ai_health = _build_ai_health()
    steps_output: List[Dict[str, Any]] = []
    completed = 0
    failed = 0

    create_or_update_snapshot(
        mission_id=payload.mission_id,
        objective=payload.objective,
        steps=[],
        status="running",
        active_step_id=(payload.steps[0].step_id if payload.steps else None),
        error=None,
    )
    log_event(payload.mission_id, f"mission started: objective={payload.objective}")

    try:
        for step in payload.steps:
            touch_heartbeat(payload.mission_id)
            log_event(payload.mission_id, f"step started: {step.step_id}")

            try:
                resolved_provider = _resolve_provider(step.preferred_provider, ai_health)
                response_text = _generate_response_text(payload.objective, step, resolved_provider)
                routed = _execute_planned_chain(payload.objective, step)

                step_status = routed["step_status"]
                step_error = routed["step_error"]

                step_payload = _json_safe(
                    {
                        "step_id": step.step_id,
                        "title": step.title,
                        "description": step.description,
                        "task_type": step.task_type,
                        "preferred_provider": step.preferred_provider,
                        "status": step_status,
                        "response_text": response_text,
                        "error": step_error,
                        "metadata": {
                            **dict(step.metadata or {}),
                            "requested_provider": step.preferred_provider,
                            "resolved_provider": resolved_provider,
                            "fallback_used": resolved_provider != (step.preferred_provider or "").strip().lower(),
                            "tool": routed["tool"],
                            "tool_payload": routed["tool_payload"],
                            "tool_result": routed["tool_result"],
                            "tool_chain_plan": routed["tool_chain_plan"],
                            "tool_chain_result": routed["tool_chain_result"],
                            "routing_source": routed["routing_source"],
                            "routing_reason": routed["routing_reason"],
                            "routing_confidence": routed["routing_confidence"],
                            "routing_fallback_count": routed["routing_fallback_count"],
                            "execution_attempts": routed["execution_attempts"],
                            "chain_attempts": routed["chain_attempts"],
                        },
                    }
                )

            except Exception as exc:
                step_status = "failed"
                step_error = f"Unexpected step failure: {exc}"
                step_payload = _json_safe(
                    {
                        "step_id": step.step_id,
                        "title": step.title,
                        "description": step.description,
                        "task_type": step.task_type,
                        "preferred_provider": step.preferred_provider,
                        "status": step_status,
                        "response_text": "",
                        "error": step_error,
                        "metadata": {
                            **dict(step.metadata or {}),
                            "tool": None,
                            "tool_payload": {},
                            "tool_result": None,
                            "tool_chain_plan": [],
                            "tool_chain_result": None,
                            "routing_source": "error",
                            "routing_reason": "Unexpected exception in chained multistep execution.",
                            "routing_confidence": 0.0,
                            "routing_fallback_count": 0,
                            "execution_attempts": [],
                            "chain_attempts": [],
                        },
                    }
                )

            steps_output.append(step_payload)

            if step_status == "completed":
                completed += 1
                log_event(payload.mission_id, f"step completed: {step.step_id}")
            else:
                failed += 1
                log_event(payload.mission_id, f"step failed: {step.step_id}, error={step_error}")

            create_or_update_snapshot(
                mission_id=payload.mission_id,
                objective=payload.objective,
                steps=steps_output,
                status="running" if failed == 0 else "partial_failure",
                active_step_id=step.step_id,
                error=None if failed == 0 else "One or more execution steps failed",
            )

        final_status = "completed" if failed == 0 else "partial_failure"
        final_summary = f"Multi-step execution for '{payload.objective}' finished. Completed steps: {completed}. Failed steps: {failed}. Overall status: {final_status}."

        create_or_update_snapshot(
            mission_id=payload.mission_id,
            objective=payload.objective,
            steps=steps_output,
            status=final_status,
            active_step_id=None,
            error=None if failed == 0 else "One or more execution steps failed",
        )
        log_event(payload.mission_id, f"mission finished: status={final_status}")

        packaged = package_mission_result(
            mission_id=payload.mission_id,
            objective=payload.objective,
            steps=steps_output,
            final_summary=final_summary,
            ok=(failed == 0),
            error=None if failed == 0 else "One or more execution steps failed",
        )

        return {
            "ok": failed == 0,
            "mission_id": payload.mission_id,
            "objective": payload.objective,
            "steps": steps_output,
            "final_summary": final_summary,
            "memory_written": True,
            "error": None if failed == 0 else "One or more execution steps failed",
            "packaged_result": packaged,
        }
    finally:
        release_lock(payload.mission_id)


@router.post("/api/missions/multistep/execute")
def execute_missions_multistep(payload: MultiStepMissionRequest) -> Dict[str, Any]:
    return _execute_multistep(payload)


@router.post("/api/missions/multi-step/execute")
def execute_missions_multi_step(payload: MultiStepMissionRequest) -> Dict[str, Any]:
    return _execute_multistep(payload)


@router.post("/api/autonomy/missions/multistep/execute")
def execute_autonomy_missions_multistep(payload: MultiStepMissionRequest) -> Dict[str, Any]:
    return _execute_multistep(payload)


@router.post("/api/autonomy/multi-step/execute")
def execute_autonomy_multi_step(payload: MultiStepMissionRequest) -> Dict[str, Any]:
    return _execute_multistep(payload)