from typing import Dict

from app.services.policy import get_policy


def compute_backoff_seconds(attempt_count: int) -> float:
    policy = get_policy()
    base = float(policy.get("retry_base_seconds", 2.0))
    cap = float(policy.get("retry_max_seconds", 20.0))
    attempt = max(1, int(attempt_count))
    value = base * (2 ** (attempt - 1))
    return min(value, cap)


def retry_allowed(attempt_count: int, max_retries: int | None = None) -> bool:
    policy = get_policy()
    limit = int(max_retries if max_retries is not None else policy.get("max_retries", 2))
    return int(attempt_count) <= limit


def retry_meta(attempt_count: int, max_retries: int | None = None) -> Dict[str, float | int | bool]:
    return {
        "attempt_count": int(attempt_count),
        "allowed": retry_allowed(attempt_count, max_retries=max_retries),
        "backoff_seconds": compute_backoff_seconds(attempt_count),
    }