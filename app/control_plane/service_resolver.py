from __future__ import annotations

import json
from pathlib import Path


class ServiceResolver:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.policy_path = self.base_dir / "adaptive_policy.json"

    def _load_policy(self) -> dict:
        if not self.policy_path.exists():
            return {}
        try:
            return json.loads(self.policy_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _handoff_map(self, notes) -> dict[str, str]:
        out = {}
        for item in list(notes or []):
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def resolve_actual_service(
        self,
        agent_id: str = "",
        capability: str = "",
        task_type: str = "",
        provider: str = "",
        preferred_service: str = "",
        handoff_notes=None,
    ) -> str:
        handoff = self._handoff_map(handoff_notes or [])
        if handoff.get("service"):
            return handoff["service"]

        provider = str(provider or "").strip().lower()
        preferred_service = str(preferred_service or "").strip()
        capability = str(capability or "").strip()
        task_type = str(task_type or "").strip()
        agent_id = str(agent_id or "").strip()

        if preferred_service:
            return preferred_service

        if provider == "ollama":
            return "ollama"

        if capability in {"memory_write", "context_bundle", "obsidian_sync"}:
            return "ollama"

        if capability in {"workflow_run", "integration_bind"} or task_type in {"integration", "connector_execution", "service_probe"}:
            if preferred_service:
                return preferred_service

        policy = self._load_policy()
        connector_prefs = ((policy.get("connector_preferences") or {}).get(agent_id) or {})
        preferred = str(connector_prefs.get(capability) or "").strip()
        if preferred:
            return preferred

        if capability in {"plan", "validate", "review", "smoke_test"}:
            return "openai"

        if capability in {"codegen", "patch", "refactor", "debug", "analyze", "compare", "summarize"}:
            return "claude_bridge"

        if capability in {"workflow_run", "automation"}:
            return "n8n"

        if provider == "cloud":
            return "openai"

        return provider or "unknown"

    def candidate_services(self, task, agent_id: str) -> list[str]:
        policy = self._load_policy()
        connector_prefs = ((policy.get("connector_preferences") or {}).get(agent_id) or {})

        capability = str(task.required_capability or "").strip()
        task_type = str(task.task_type or "").strip()
        explicit = str(task.metadata.get("preferred_service") or "").strip()
        suggested = str(((task.metadata.get("learning_guidance") or {}).get("suggested_service")) or "").strip()
        configured_pref = str(connector_prefs.get(capability) or "").strip()

        ordered = []
        for item in [explicit, suggested, configured_pref]:
            if item and item not in ordered:
                ordered.append(item)

        if capability in {"memory_write", "context_bundle", "obsidian_sync"}:
            ordered += ["ollama"]
        elif capability in {"plan", "validate", "review", "smoke_test"}:
            ordered += ["openai", "claude_bridge", "ollama"]
        elif capability in {"codegen", "patch", "refactor", "debug", "analyze", "compare", "summarize"}:
            ordered += ["claude_bridge", "openai", "ollama"]
        elif capability in {"workflow_run", "automation"} or task_type in {"integration", "connector_execution"}:
            ordered += ["n8n", "claude_bridge", "openai", "ollama"]
        else:
            ordered += ["openai", "claude_bridge", "ollama"]

        out = []
        seen = set()
        for item in ordered:
            if item and item not in seen:
                out.append(item)
                seen.add(item)
        return out