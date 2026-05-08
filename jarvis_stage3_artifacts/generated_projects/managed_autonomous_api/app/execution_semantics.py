from __future__ import annotations

from dataclasses import dataclass
from typing import Any


NON_RETRYABLE_CATEGORIES = {
    "configuration",
    "policy_block",
    "input",
    "permission",
}

RETRYABLE_CATEGORIES = {
    "transient",
    "unknown",
}


@dataclass
class RetryDecision:
    retryable: bool
    category: str
    delay_seconds: float
    reason: str


def classify_error_message(message: str) -> str:
    text = (message or "").lower()

    if "assigned agent not found" in text or "disabled: critic_agent" in text:
        return "configuration"
    if "unsafe shell command blocked" in text or "deny pattern" in text:
        return "policy_block"
    if "validation" in text or "422" in text or "bad request" in text:
        return "input"
    if "forbidden" in text or "permission" in text or "denied" in text:
        return "permission"
    if "timeout" in text or "temporarily unavailable" in text or "connection reset" in text:
        return "transient"
    return "unknown"


def compute_retry_delay(attempt: int, base_seconds: float = 2.0, max_seconds: float = 60.0) -> float:
    attempt = max(1, int(attempt))
    delay = base_seconds * (2 ** (attempt - 1))
    if delay > max_seconds:
        delay = max_seconds
    return round(delay, 2)


def should_retry_error(
    message: str,
    attempt: int,
    max_attempts: int = 4,
    base_seconds: float = 2.0,
    max_seconds: float = 60.0,
) -> RetryDecision:
    category = classify_error_message(message)

    if category in NON_RETRYABLE_CATEGORIES:
        return RetryDecision(
            retryable=False,
            category=category,
            delay_seconds=0.0,
            reason="Non-retryable error category",
        )

    if attempt >= max_attempts:
        return RetryDecision(
            retryable=False,
            category=category,
            delay_seconds=0.0,
            reason="Retry limit reached",
        )

    return RetryDecision(
        retryable=True,
        category=category,
        delay_seconds=compute_retry_delay(
            attempt=attempt,
            base_seconds=base_seconds,
            max_seconds=max_seconds,
        ),
        reason="Retry allowed",
    )


def recompute_mission_status(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "task_count": len(tasks),
        "queued": 0,
        "running": 0,
        "retrying": 0,
        "completed": 0,
        "failed": 0,
        "all_completed": False,
        "has_blocking_failure": False,
        "recommended_mission_status": "created",
    }

    for task in tasks:
        status = str(task.get("status") or "").lower()
        if status == "queued":
            summary["queued"] += 1
        elif status == "running":
            summary["running"] += 1
        elif status == "retrying":
            summary["retrying"] += 1
        elif status == "completed":
            summary["completed"] += 1
        elif status == "failed":
            summary["failed"] += 1

    summary["all_completed"] = (
        summary["task_count"] > 0
        and summary["completed"] == summary["task_count"]
    )

    summary["has_blocking_failure"] = summary["failed"] > 0

    if summary["all_completed"]:
        summary["recommended_mission_status"] = "completed"
    elif summary["has_blocking_failure"]:
        summary["recommended_mission_status"] = "failed"
    elif summary["running"] > 0 or summary["retrying"] > 0:
        summary["recommended_mission_status"] = "running"
    elif summary["queued"] > 0:
        summary["recommended_mission_status"] = "created"
    else:
        summary["recommended_mission_status"] = "created"

    return summary
