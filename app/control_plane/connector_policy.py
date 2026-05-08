from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field

from .adaptive_policy import AdaptivePolicyStore
from .connector_registry import ConnectorRegistry


class ConnectorRouteDecision(BaseModel):
    connector_id: str = ""
    service: str = ""
    reason: str = ""
    fallback_connectors: list[str] = Field(default_factory=list)


class ConnectorPolicyRouter:
    LOCAL_ONLY_CAPABILITIES = {
        "memory_write",
        "context_bundle",
        "obsidian_sync",
        "file_read",
        "file_write",
        "shell_safe",
    }

    def __init__(
        self,
        connector_registry: ConnectorRegistry,
        policy_path: str | Path = "state/agent_mesh/adaptive_policy.json",
    ) -> None:
        self.connector_registry = connector_registry
        self.policy = AdaptivePolicyStore(Path(policy_path))

    def _is_capable(self, connector, capability: str, preferred_service: str = "") -> bool:
        if connector.allowed_capabilities and capability not in connector.allowed_capabilities:
            return False

        # Prevent overly broad ollama fallback for non-local integration capabilities
        if (
            connector.service == "ollama"
            and not connector.allowed_capabilities
            and preferred_service != "ollama"
            and capability not in self.LOCAL_ONLY_CAPABILITIES
        ):
            return False

        return True

    def select(self, agent_id: str, capability: str, preferred_service: str = "") -> ConnectorRouteDecision:
        adaptive_preferred = self.policy.get_connector_preference(agent_id, capability)
        preferred = preferred_service or adaptive_preferred

        candidates = self.connector_registry.list_for_agent(agent_id)

        capable = []
        for c in candidates:
            if self._is_capable(c, capability, preferred_service=preferred):
                capable.append(c)

        if preferred:
            for c in capable:
                if c.service == preferred:
                    fallbacks = [x.connector_id for x in capable if x.connector_id != c.connector_id]
                    return ConnectorRouteDecision(
                        connector_id=c.connector_id,
                        service=c.service,
                        reason=f"preferred_service:{preferred}",
                        fallback_connectors=fallbacks,
                    )

        for preferred_service_name in [
            "claude_bridge",
            "openai",
            "google_workspace",
            "telegram",
            "n8n",
            "ollama",
        ]:
            for c in capable:
                if c.service == preferred_service_name:
                    fallbacks = [x.connector_id for x in capable if x.connector_id != c.connector_id]
                    return ConnectorRouteDecision(
                        connector_id=c.connector_id,
                        service=c.service,
                        reason=f"capability_match:{capability}",
                        fallback_connectors=fallbacks,
                    )

        if capable:
            first = capable[0]
            return ConnectorRouteDecision(
                connector_id=first.connector_id,
                service=first.service,
                reason="first_available_capable_connector",
                fallback_connectors=[x.connector_id for x in capable if x.connector_id != first.connector_id],
            )

        return ConnectorRouteDecision(
            connector_id="",
            service="",
            reason=f"no_connector_for_agent={agent_id}_capability={capability}",
            fallback_connectors=[],
        )