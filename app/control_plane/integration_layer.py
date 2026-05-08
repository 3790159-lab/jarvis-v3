from __future__ import annotations

from pathlib import Path

from .auth_manager import AuthManager
from .connector_registry import AuthType, ConnectorBinding, ConnectorRegistry, ConnectorStatus
from .secrets import SecretRegistry


def bootstrap_default_connectors(connectors: ConnectorRegistry, secrets: SecretRegistry) -> None:
    defaults = [
        ConnectorBinding(
            connector_id="openai_primary",
            service="openai",
            auth_type=AuthType.API_KEY,
            status=ConnectorStatus.ENABLED,
            allowed_agents=["coding_agent_main", "research_agent_main", "qa_agent_main", "planner_main"],
            allowed_capabilities=["codegen", "analyze", "validate", "plan"],
            metadata={"secret_name": "openai_api_key", "requires_secret": True},
        ),
        ConnectorBinding(
            connector_id="claude_bridge_primary",
            service="claude_bridge",
            auth_type=AuthType.API_KEY,
            status=ConnectorStatus.ENABLED,
            allowed_agents=["coding_agent_main", "planner_main", "research_agent_main"],
            allowed_capabilities=["codegen", "plan", "analyze"],
            metadata={"secret_name": "claude_api_key", "requires_secret": True},
        ),
        ConnectorBinding(
            connector_id="ollama_local",
            service="ollama",
            auth_type=AuthType.LOCAL_BRIDGE,
            status=ConnectorStatus.ENABLED,
            allowed_agents=[],
            allowed_capabilities=[],
            metadata={"base_url": "http://127.0.0.1:11434", "requires_secret": False},
        ),
        ConnectorBinding(
            connector_id="n8n_primary",
            service="n8n",
            auth_type=AuthType.WEBHOOK,
            status=ConnectorStatus.ENABLED,
            allowed_agents=["n8n_agent_main", "integration_agent_main"],
            allowed_capabilities=["automation", "workflow_run", "webhook_invoke"],
            metadata={
                "secret_name": "n8n_api_key",
                "base_url": "http://127.0.0.1:5678",
                "requires_secret": True,
                "webhook_path": ""
            },
        ),
        ConnectorBinding(
            connector_id="google_workspace",
            service="google_workspace",
            auth_type=AuthType.SERVICE_ACCOUNT,
            status=ConnectorStatus.ENABLED,
            allowed_agents=["spreadsheet_agent_main", "integration_agent_main"],
            allowed_capabilities=["sheet_read", "sheet_write", "sheet_format", "oauth_binding"],
            metadata={"secret_name": "google_service_account_json", "requires_secret": True},
        ),
        ConnectorBinding(
            connector_id="telegram_bot",
            service="telegram",
            auth_type=AuthType.API_KEY,
            status=ConnectorStatus.ENABLED,
            allowed_agents=["integration_agent_main"],
            allowed_capabilities=["api_invoke", "integration_bind"],
            metadata={"secret_name": "telegram_bot_token", "requires_secret": True},
        ),
    ]

    for item in defaults:
        connectors.upsert(item)

    secrets.set_secret_ref("openai_api_key", env_var="OPENAI_API_KEY", note="Primary OpenAI key")
    secrets.set_secret_ref("claude_api_key", env_var="ANTHROPIC_API_KEY", note="Claude/Anthropic key")
    secrets.set_secret_ref("n8n_api_key", env_var="N8N_API_KEY", note="n8n API key")
    secrets.set_secret_ref("google_service_account_json", env_var="GOOGLE_SERVICE_ACCOUNT_JSON", note="Google Workspace service account JSON path")
    secrets.set_secret_ref("telegram_bot_token", env_var="TELEGRAM_BOT_TOKEN", note="Telegram bot token")


def build_auth_manager(base_dir: Path) -> AuthManager:
    connectors = ConnectorRegistry(base_dir / "connectors.json")
    secrets = SecretRegistry(base_dir / "secrets_registry.json")
    bootstrap_default_connectors(connectors, secrets)
    return AuthManager(connectors, secrets)