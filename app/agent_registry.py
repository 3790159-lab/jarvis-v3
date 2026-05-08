from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .message_models import ExecutionResult


@dataclass
class AgentSpec:
    name: str
    description: str
    handler: Callable[..., ExecutionResult]


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentSpec] = {}

    def register(self, name: str, description: str, handler: Callable[..., ExecutionResult]) -> None:
        self._agents[name] = AgentSpec(name=name, description=description, handler=handler)

    def call(self, name: str, **kwargs) -> ExecutionResult:
        if name not in self._agents:
            return ExecutionResult(ok=False, tool="agent_call", summary=f"Агент '{name}' не найден")
        return self._agents[name].handler(**kwargs)

    def list_agents(self) -> list[dict[str, str]]:
        return [
            {"name": agent.name, "description": agent.description}
            for agent in self._agents.values()
        ]
