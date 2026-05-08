from __future__ import annotations

import json
import time
from pathlib import Path

from .adaptive_policy import AdaptivePolicyStore
from .claude_payload_probe import ClaudePayloadProbe
from .external_call_guard import ExternalCallGuard
from .lifecycle_manager import LifecycleManager
from .service_telemetry import ServiceTelemetryStore


class SelfHealingEngine:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.state_path = self.base_dir / "self_healing_state.json"
        self.telemetry = ServiceTelemetryStore(self.base_dir / "service_telemetry.json")
        self.guard = ExternalCallGuard(self.base_dir)
        self.policy = AdaptivePolicyStore(self.base_dir / "adaptive_policy.json")
        self.lifecycle = LifecycleManager(self.base_dir)
        self.claude_probe = ClaudePayloadProbe(self.base_dir)
        self._ensure()

    def _ensure(self) -> None:
        if not self.state_path.exists():
            self.state_path.write_text(json.dumps({
                "openai_endpoint_compatibility": True,
                "anthropic_safe_mode": False,
                "force_specific_service_resolution": True,
                "anthropic_payload_profile": "safe_inline",
                "continuous_mode": True,
                "last_tick_ts": 0.0,
                "last_actions": [],
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> dict:
        self._ensure()
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def health(self) -> dict:
        data = self.load()
        data["claude_probe"] = self.claude_probe.health()
        return dict(data)

    def tick(self, reason: str = "manual") -> dict:
        data = self.load()
        telemetry = self.telemetry.summary()
        services = {x.get("service"): x for x in telemetry.get("services", [])}
        actions = []

        claude = services.get("claude_bridge", {})
        if int(claude.get("failure_count", 0) or 0) >= 2 and "400" in str(claude.get("last_error", "") or ""):
            data["anthropic_safe_mode"] = True
            data["anthropic_payload_profile"] = "safe_inline"
            self.claude_probe.set_active_profile("safe_inline")
            actions.append("Enabled anthropic_safe_mode and active Claude payload profile safe_inline due to repeated Claude 400 errors.")

        openai = services.get("openai", {})
        if int(openai.get("failure_count", 0) or 0) >= 1 or int(openai.get("fallback_count", 0) or 0) >= 1:
            data["openai_endpoint_compatibility"] = True
            actions.append("Confirmed openai_endpoint_compatibility due to fallback/failure signal.")

        unknown = services.get("unknown", {})
        if int(unknown.get("failure_count", 0) or 0) > 0:
            data["force_specific_service_resolution"] = True
            actions.append("Kept force_specific_service_resolution enabled because unknown-service failures exist.")

        life = self.lifecycle.health()
        if int(life.get("active_snapshots", 0) or 0) >= 8:
            cleanup = self.lifecycle.cleanup(
                retain_recent_completed=8,
                max_journal_lines_per_mission=400,
                max_exec_log_lines=650,
            )
            actions.append(f"Lifecycle cleanup executed: archived_after={cleanup.get('archived_after', 0)} active_after={cleanup.get('active_after', 0)}")

        try:
            notes = list(self.policy.policy.notes)
            marker = "Self-healing reviewed external agent stability."
            if marker not in notes:
                notes.append(marker)
            self.policy.set_notes(notes)
            self.policy.save()
        except Exception:
            pass

        data["continuous_mode"] = True
        data["last_tick_ts"] = time.time()
        data["last_actions"] = actions
        self.save(data)

        return {
            "status": "ok",
            "reason": reason,
            "actions": actions,
            "state": data,
            "telemetry_summary": telemetry,
            "claude_probe": self.claude_probe.health(),
        }