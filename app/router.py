from __future__ import annotations

from app.executors.base import BaseExecutor
from app.executors.codex_cloud import CodexCloudExecutor
from app.executors.codex_local import CodexLocalExecutor
from app.executors.local_shell import LocalShellExecutor
from app.executors.noop import NoopExecutor


def get_executor(name: str) -> BaseExecutor:
    registry: dict[str, BaseExecutor] = {
        "shell_local": LocalShellExecutor(),
        "codex_local": CodexLocalExecutor(),
        "codex_cloud": CodexCloudExecutor(),
        "noop": NoopExecutor(),
    }
    if name not in registry:
        raise ValueError(f"Unknown executor: {name}")
    return registry[name]
