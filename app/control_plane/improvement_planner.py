from __future__ import annotations

import json
import time
from pathlib import Path


class ImprovementPlanner:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "improvement_proposals.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"items": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"items": []}

    def _save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def analyze(self, telemetry_summary: dict, lifecycle_health: dict, healing_state: dict, n8n_health: dict | None = None) -> dict:
        services = {x.get("service"): x for x in telemetry_summary.get("services", [])}
        items = []

        cloud = services.get("cloud", {})
        if int(cloud.get("success_count", 0) or 0) >= 20:
            items.append({
                "id": "reduce_cloud_ambiguity",
                "priority": "high",
                "title": "Reduce generic cloud routing labels",
                "reason": "Too many executions still resolve as generic cloud instead of specific service.",
                "suggested_action": "Prefer direct service stamping in runtime and keep trace rebuild active.",
            })

        unknown = services.get("unknown", {})
        if int(unknown.get("failure_count", 0) or 0) > 0:
            items.append({
                "id": "eliminate_unknown_service_failures",
                "priority": "high",
                "title": "Eliminate unknown service failures",
                "reason": "Unknown service failures still appear in telemetry.",
                "suggested_action": "Strengthen service resolver fallback and keep force_specific_service_resolution enabled.",
            })

        claude = services.get("claude_bridge", {})
        if int(claude.get("failure_count", 0) or 0) >= 2 and "400" in str(claude.get("last_error", "") or ""):
            items.append({
                "id": "anthropic_payload_compatibility",
                "priority": "high",
                "title": "Improve Claude payload compatibility",
                "reason": "Repeated Claude 400 errors are still present.",
                "suggested_action": "Keep anthropic_safe_mode on and validate candidate payload shapes before invoke.",
            })

        n8n = services.get("n8n", {})
        if int(n8n.get("failure_count", 0) or 0) > 0:
            items.append({
                "id": "n8n_live_guarded_rollout",
                "priority": "medium",
                "title": "Keep n8n rollout guarded",
                "reason": "n8n still has failures and should remain gated until verify/test are stable.",
                "suggested_action": "Do not promote live until verify_api and dry-run webhook both succeed.",
            })

        if n8n_health:
            if not bool((n8n_health or {}).get("configured", False)):
                items.append({
                    "id": "n8n_missing_config",
                    "priority": "high",
                    "title": "Complete n8n connector config",
                    "reason": "n8n still is not fully configured.",
                    "suggested_action": "Persist webhook_path and ensure n8n is reachable on the configured base_url.",
                })

        if int(lifecycle_health.get("active_snapshots", 0) or 0) >= 8:
            items.append({
                "id": "active_snapshot_pressure",
                "priority": "medium",
                "title": "Reduce active snapshot pressure",
                "reason": "Active snapshots remain elevated.",
                "suggested_action": "Keep aggressive cleanup and archive completed missions earlier.",
            })

        report = {
            "ts": time.time(),
            "proposal_count": len(items),
            "items": items,
            "healing_state": healing_state,
        }

        self._save(report)
        return report

    def load_report(self) -> dict:
        return self._load()