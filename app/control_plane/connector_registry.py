from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ConnectorStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    UNCONFIGURED = "unconfigured"


class AuthType(str, Enum):
    API_KEY = "api_key"
    OAUTH = "oauth"
    SERVICE_ACCOUNT = "service_account"
    LOCAL_BRIDGE = "local_bridge"
    WEBHOOK = "webhook"
    TOKEN = "token"


class ConnectorBinding(BaseModel):
    connector_id: str
    service: str
    auth_type: AuthType
    status: ConnectorStatus = ConnectorStatus.UNCONFIGURED
    scopes: list[str] = Field(default_factory=list)
    allowed_agents: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.connectors: dict[str, ConnectorBinding] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"connectors": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.connectors = {}
        for item in raw.get("connectors", []):
            c = ConnectorBinding.model_validate(item)
            self.connectors[c.connector_id] = c

    def save(self) -> None:
        data = {"connectors": [c.model_dump(mode="json") for c in self.connectors.values()]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def upsert(self, binding: ConnectorBinding) -> None:
        self.connectors[binding.connector_id] = binding
        self.save()

    def get(self, connector_id: str) -> ConnectorBinding | None:
        return self.connectors.get(connector_id)

    def list_all(self) -> list[ConnectorBinding]:
        return list(self.connectors.values())

    def list_for_agent(self, agent_id: str) -> list[ConnectorBinding]:
        return [
            c for c in self.connectors.values()
            if (not c.allowed_agents or agent_id in c.allowed_agents)
        ]