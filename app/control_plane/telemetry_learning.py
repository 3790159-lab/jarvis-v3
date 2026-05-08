from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .adaptive_policy import AdaptivePolicyStore
from .auth_manager import AuthManager
from .provider_health import ProviderHealthRegistry


class TelemetryLearner:
    def __init__(
        self,
        base_dir: Path,
        policy_store: AdaptivePolicyStore,
        auth_manager: AuthManager,
        provider_health: ProviderHealthRegistry,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.policy_store = policy_store
        self.auth_manager = auth_manager
        self.provider_health = provider_health
        self.exec_log_path = self.base_dir / "connector_execution_log.jsonl"

    def _read_exec_log(self, limit: int = 300) -> list[dict[str, Any]]:
        if not self.exec_log_path.exists():
            return []
        lines = self.exec_log_path.read_text(encoding="utf-8").splitlines()
        selected = lines[-max(1, limit):]
        out: list[dict[str, Any]] = []
        for line in selected:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def rebuild_policy(self) -> dict[str, Any]:
        self.policy_store.reset()

        notes: list[str] = []
        auth = {x["connector_id"]: x for x in self.auth_manager.list_connector_health()}
        providers = self.provider_health.summary().get("providers", [])
        provider_state = {p["provider"]: p for p in providers}

        events = self._read_exec_log(limit=400)
        live_ok_by_service: dict[str, float] = defaultdict(float)
        dry_ok_by_service: dict[str, float] = defaultdict(float)
        fail_by_service: dict[str, float] = defaultdict(float)

        for ev in events:
            service = str(ev.get("service") or "")
            if not service:
                continue

            ok = bool(ev.get("ok", False))
            dry_run = bool(ev.get("dry_run", False))
            configured = bool(ev.get("configured", False))

            if ok and not dry_run and configured:
                live_ok_by_service[service] += 1.0
            elif ok and dry_run:
                dry_ok_by_service[service] += 0.25
            elif not ok:
                fail_by_service[service] += 1.0

        cloud_ok = provider_state.get("cloud", {}).get("status", "healthy") == "healthy"
        openai_compat_ok = provider_state.get("openai_compatible", {}).get("status", "healthy") == "healthy"

        if cloud_ok:
            for cap in ["plan", "analyze", "codegen", "validate", "automation", "workflow_run", "integration_bind"]:
                self.policy_store.set_provider_preference(cap, "cloud")
        elif openai_compat_ok:
            for cap in ["plan", "analyze", "codegen", "validate", "automation", "workflow_run", "integration_bind"]:
                self.policy_store.set_provider_preference(cap, "openai_compatible")
            notes.append("Cloud degraded: shifted reasoning-heavy capabilities to openai_compatible.")
        else:
            for cap in ["plan", "analyze", "codegen", "validate", "automation", "workflow_run", "integration_bind"]:
                self.policy_store.set_provider_preference(cap, "ollama")
            notes.append("Online providers degraded: shifted reasoning-heavy capabilities to ollama fallback.")

        for cap in ["memory_write", "context_bundle", "obsidian_sync"]:
            self.policy_store.set_provider_preference(cap, "ollama")

        claude_configured = auth.get("claude_bridge_primary", {}).get("configured", False)
        openai_configured = auth.get("openai_primary", {}).get("configured", False)
        n8n_configured = auth.get("n8n_primary", {}).get("configured", False)
        google_configured = auth.get("google_workspace", {}).get("configured", False)
        telegram_configured = auth.get("telegram_bot", {}).get("configured", False)

        if claude_configured and live_ok_by_service.get("claude_bridge", 0) >= fail_by_service.get("claude_bridge", 0):
            self.policy_store.set_connector_preference("coding_agent_main", "codegen", "claude_bridge")
            self.policy_store.set_connector_preference("research_agent_main", "analyze", "claude_bridge")
            notes.append("Claude bridge preferred for codegen/research from live telemetry.")
        elif openai_configured:
            self.policy_store.set_connector_preference("coding_agent_main", "codegen", "openai")
            self.policy_store.set_connector_preference("research_agent_main", "analyze", "openai")
            notes.append("OpenAI preferred for codegen/research fallback.")

        if openai_configured:
            self.policy_store.set_connector_preference("planner_main", "plan", "openai")
            self.policy_store.set_connector_preference("qa_agent_main", "validate", "openai")

        if google_configured:
            self.policy_store.set_connector_preference("spreadsheet_agent_main", "sheet_write", "google_workspace")
            self.policy_store.set_connector_preference("spreadsheet_agent_main", "sheet_read", "google_workspace")
            self.policy_store.set_connector_preference("spreadsheet_agent_main", "sheet_format", "google_workspace")

        if telegram_configured:
            self.policy_store.set_connector_preference("integration_agent_main", "integration_bind", "telegram")

        if n8n_configured:
            self.policy_store.set_connector_preference("n8n_agent_main", "workflow_run", "n8n")
            self.policy_store.set_connector_preference("n8n_agent_main", "automation", "n8n")
        else:
            notes.append("n8n is not fully configured yet; keep only dry-run automation routing.")

        summary = {
            "configured_connectors": [
                name for name, state in auth.items()
                if state.get("configured", False)
            ],
            "provider_states": {
                k: v.get("status", "unknown") for k, v in provider_state.items()
            },
            "recent_live_ok_by_service": dict(live_ok_by_service),
            "recent_dry_ok_by_service": dict(dry_ok_by_service),
            "recent_fail_by_service": dict(fail_by_service),
        }

        self.policy_store.set_notes(notes)
        self.policy_store.set_learning_summary(summary)
        self.policy_store.save()

        return self.policy_store.dump()

    def health(self) -> dict[str, Any]:
        policy = self.policy_store.dump()
        return {
            "status": "ok",
            "policy_version": policy.get("version", "1.0"),
            "updated_ts": policy.get("updated_ts", 0.0),
            "notes": policy.get("notes", []),
            "learning_summary": policy.get("learning_summary", {}),
        }