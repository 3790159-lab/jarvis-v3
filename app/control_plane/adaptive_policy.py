from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AdaptivePolicy(BaseModel):
    version: str = "1.0"
    updated_ts: float = 0.0
    provider_preferences: dict[str, str] = Field(default_factory=dict)
    connector_preferences: dict[str, dict[str, str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    learning_summary: dict[str, Any] = Field(default_factory=dict)


class AdaptivePolicyStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.policy = AdaptivePolicy()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.save()
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.policy = AdaptivePolicy.model_validate(raw)

    def save(self) -> None:
        self.policy.updated_ts = time.time()
        self.path.write_text(
            json.dumps(self.policy.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def reset(self) -> None:
        self.policy = AdaptivePolicy()
        self.save()

    def set_provider_preference(self, capability: str, provider: str) -> None:
        self.policy.provider_preferences[capability] = provider

    def get_provider_preference(self, capability: str) -> str:
        self.load()
        return str(self.policy.provider_preferences.get(capability) or "")

    def set_connector_preference(self, agent_id: str, capability: str, service: str) -> None:
        self.policy.connector_preferences.setdefault(agent_id, {})
        self.policy.connector_preferences[agent_id][capability] = service

    def get_connector_preference(self, agent_id: str, capability: str) -> str:
        self.load()
        return str(self.policy.connector_preferences.get(agent_id, {}).get(capability) or "")

    def set_notes(self, notes: list[str]) -> None:
        self.policy.notes = list(notes)

    def set_learning_summary(self, summary: dict[str, Any]) -> None:
        self.policy.learning_summary = dict(summary)

    def dump(self) -> dict[str, Any]:
        self.load()
        return self.policy.model_dump(mode="json")