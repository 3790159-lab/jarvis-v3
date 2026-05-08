import os
from typing import Any, Dict, List


def env_str(name: str, default: str) -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    return str(value).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_str(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = env_str(name, "true" if default else "false").lower()
    return raw in ("1", "true", "yes", "on")


PROFILE = env_str("SUPERVISOR_POLICY_PROFILE", "safe").lower()

_PROFILE_PRESETS = {
    "safe": {
        "shell_enabled": False,
        "max_retries": 2,
        "retry_base_seconds": 2.0,
        "retry_max_seconds": 20.0,
        "worker_poll_interval_seconds": 2.0,
        "worker_count": 2,
        "allow_file_write": True,
        "allow_shell_prefixes": [],
        "max_text_artifact_chars": 12000,
        "llm_enabled": True,
    },
    "dev": {
        "shell_enabled": True,
        "max_retries": 3,
        "retry_base_seconds": 1.5,
        "retry_max_seconds": 15.0,
        "worker_poll_interval_seconds": 1.5,
        "worker_count": 2,
        "allow_file_write": True,
        "allow_shell_prefixes": ["echo ", "dir", "Get-ChildItem", "pwd", "where.exe ", "python --version"],
        "max_text_artifact_chars": 18000,
        "llm_enabled": True,
    },
    "admin": {
        "shell_enabled": True,
        "max_retries": 4,
        "retry_base_seconds": 1.0,
        "retry_max_seconds": 12.0,
        "worker_poll_interval_seconds": 1.0,
        "worker_count": 3,
        "allow_file_write": True,
        "allow_shell_prefixes": ["echo ", "dir", "Get-ChildItem", "pwd", "where.exe ", "python --version", "type ", "Get-Content "],
        "max_text_artifact_chars": 24000,
        "llm_enabled": True,
    },
}

_DEFAULTS = _PROFILE_PRESETS.get(PROFILE, _PROFILE_PRESETS["safe"])


def get_policy() -> Dict[str, Any]:
    policy = dict(_DEFAULTS)
    policy["profile"] = PROFILE
    policy["shell_enabled"] = env_bool("POLICY_SHELL_ENABLED", policy["shell_enabled"])
    policy["max_retries"] = env_int("POLICY_MAX_RETRIES", policy["max_retries"])
    policy["retry_base_seconds"] = env_float("POLICY_RETRY_BASE_SECONDS", policy["retry_base_seconds"])
    policy["retry_max_seconds"] = env_float("POLICY_RETRY_MAX_SECONDS", policy["retry_max_seconds"])
    policy["worker_poll_interval_seconds"] = env_float("WORKER_POLL_INTERVAL_SECONDS", policy["worker_poll_interval_seconds"])
    policy["worker_count"] = env_int("WORKER_COUNT", policy["worker_count"])
    policy["allow_file_write"] = env_bool("POLICY_ALLOW_FILE_WRITE", policy["allow_file_write"])
    policy["max_text_artifact_chars"] = env_int("POLICY_MAX_TEXT_ARTIFACT_CHARS", policy["max_text_artifact_chars"])
    policy["llm_enabled"] = env_bool("POLICY_LLM_ENABLED", policy["llm_enabled"])

    prefixes_raw = env_str("POLICY_ALLOW_SHELL_PREFIXES", ",".join(policy["allow_shell_prefixes"]))
    policy["allow_shell_prefixes"] = [p.strip() for p in prefixes_raw.split(",") if p.strip()]
    return policy


def allowed_task_types() -> List[str]:
    return [
        "analysis",
        "integration",
        "backend",
        "llm",
        "planning",
        "review",
        "file_write",
        "shell",
        "generic",
    ]