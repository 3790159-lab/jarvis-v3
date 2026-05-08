from __future__ import annotations

from .connector_registry import ConnectorRegistry
from .secrets import SecretRegistry


class AuthManager:
    def __init__(self, connector_registry: ConnectorRegistry, secret_registry: SecretRegistry) -> None:
        self.connector_registry = connector_registry
        self.secret_registry = secret_registry

    def connector_health(self, connector_id: str) -> dict:
        connector = self.connector_registry.get(connector_id)
        if not connector:
            return {"ok": False, "message": f"Connector not found: {connector_id}"}

        secret_name = str(connector.metadata.get("secret_name") or "")
        requires_secret = bool(connector.metadata.get("requires_secret", False))
        base_url = str(connector.metadata.get("base_url") or "")

        secret_state = self.secret_registry.resolve(secret_name) if secret_name else {
            "name": "",
            "value_present": False,
            "value_source": "none",
            "note": "",
        }

        configured = False
        if connector.auth_type.value == "local_bridge":
            configured = bool(base_url)
        elif connector.auth_type.value == "webhook":
            configured = bool(base_url) and ((not requires_secret) or secret_state["value_present"])
        else:
            configured = bool(secret_name and secret_state["value_present"])

        return {
            "ok": configured,
            "connector_id": connector.connector_id,
            "service": connector.service,
            "auth_type": connector.auth_type.value,
            "status": connector.status.value,
            "configured": configured,
            "secret_state": secret_state,
            "requires_secret": requires_secret,
            "base_url_present": bool(base_url),
        }

    def list_connector_health(self) -> list[dict]:
        return [self.connector_health(c.connector_id) for c in self.connector_registry.list_all()]