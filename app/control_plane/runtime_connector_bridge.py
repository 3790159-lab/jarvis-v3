from __future__ import annotations

from pydantic import BaseModel

from .connector_executor import ConnectorExecutor
from .connector_policy import ConnectorPolicyRouter
from .provider_health import ProviderHealthRegistry


class RuntimeConnectorOutcome(BaseModel):
    ok: bool
    connector_id: str = ""
    service: str = ""
    message: str = ""
    output: dict = {}


class RuntimeConnectorBridge:
    def __init__(
        self,
        policy_router: ConnectorPolicyRouter,
        connector_executor: ConnectorExecutor,
        provider_health: ProviderHealthRegistry,
    ) -> None:
        self.policy_router = policy_router
        self.connector_executor = connector_executor
        self.provider_health = provider_health

    def _map_service_to_provider_bucket(self, service: str) -> str:
        if service in {"openai", "claude_bridge"}:
            return "cloud"
        if service == "ollama":
            return "ollama"
        return ""

    def _record(self, service: str, ok: bool, message: str) -> None:
        bucket = self._map_service_to_provider_bucket(service)
        if not bucket:
            return
        if ok:
            self.provider_health.record_success(bucket, latency_ms=1.0)
        else:
            self.provider_health.record_failure(bucket, message)

    def run(
        self,
        agent_id: str,
        capability: str,
        action: str,
        payload: dict | None = None,
        preferred_service: str = "",
        dry_run: bool = True,
    ) -> RuntimeConnectorOutcome:
        route = self.policy_router.select(
            agent_id=agent_id,
            capability=capability,
            preferred_service=preferred_service,
        )

        if not route.connector_id:
            return RuntimeConnectorOutcome(
                ok=False,
                connector_id="",
                service="",
                message=route.reason,
                output={},
            )

        ordered = [route.connector_id] + list(route.fallback_connectors)

        last_message = ""
        last_service = ""
        for connector_id in ordered:
            result = self.connector_executor.execute(
                agent_id=agent_id,
                connector_id=connector_id,
                capability=capability,
                action=action,
                payload=payload or {},
                dry_run=dry_run,
            )
            self._record(result.service, result.ok, result.message)

            if result.ok:
                return RuntimeConnectorOutcome(
                    ok=True,
                    connector_id=result.connector_id,
                    service=result.service,
                    message=result.message,
                    output=result.output,
                )

            last_message = result.message
            last_service = result.service

        return RuntimeConnectorOutcome(
            ok=False,
            connector_id="",
            service=last_service,
            message=last_message or "All connector attempts failed.",
            output={},
        )