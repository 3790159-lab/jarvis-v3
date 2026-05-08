from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


POLICY_PROFILES: Dict[str, dict] = {
    "safe": {
        "allow_shell": False,
        "allow_file_write": False,
        "allow_network_side_effects": False,
        "retry_base_seconds": 2.0,
        "retry_max_seconds": 30.0,
        "max_retry_attempts": 2,
    },
    "dev": {
        "allow_shell": True,
        "allow_file_write": True,
        "allow_network_side_effects": False,
        "retry_base_seconds": 2.0,
        "retry_max_seconds": 60.0,
        "max_retry_attempts": 4,
    },
    "admin": {
        "allow_shell": True,
        "allow_file_write": True,
        "allow_network_side_effects": True,
        "retry_base_seconds": 1.5,
        "retry_max_seconds": 90.0,
        "max_retry_attempts": 6,
    },
}


@dataclass
class RetryDecision:
    retryable: bool
    category: str
    delay_seconds: float
    reason: str


def get_policy_profile(profile_name: str) -> dict:
    return POLICY_PROFILES.get(profile_name, POLICY_PROFILES["dev"])


def classify_error_message(message: str) -> str:
    text = (message or "").lower()

    if "not found or disabled" in text and "agent" in text:
        return "configuration"
    if "unsafe shell command blocked" in text:
        return "policy_block"
    if "timeout" in text or "temporarily unavailable" in text or "connection reset" in text:
        return "transient"
    if "validation" in text or "422" in text or "bad request" in text:
        return "input"
    if "permission" in text or "forbidden" in text or "denied" in text:
        return "permission"
    return "unknown"


def compute_retry_delay(attempt: int, base_seconds: float = 2.0, max_seconds: float = 60.0) -> float:
    attempt = max(1, int(attempt))
    delay = base_seconds * (2 ** (attempt - 1))
    if delay > max_seconds:
        delay = max_seconds
    return round(delay, 2)


def should_retry_error(message: str, attempt: int, profile_name: str = "dev") -> RetryDecision:
    profile = get_policy_profile(profile_name)
    category = classify_error_message(message)
    max_attempts = int(profile["max_retry_attempts"])

    if category in {"configuration", "policy_block", "input", "permission"}:
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

    delay = compute_retry_delay(
        attempt=attempt,
        base_seconds=float(profile["retry_base_seconds"]),
        max_seconds=float(profile["retry_max_seconds"]),
    )

    return RetryDecision(
        retryable=True,
        category=category,
        delay_seconds=delay,
        reason="Retry allowed by policy",
    )
