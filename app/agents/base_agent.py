from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict


@dataclass
class AgentResult:
    status: str
    agent: str
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class BaseAgent:
    name = "base_agent"
    role = "generic"

    def run(self, task: Dict[str, Any]) -> AgentResult:
        raise NotImplementedError("Agent must implement run()")