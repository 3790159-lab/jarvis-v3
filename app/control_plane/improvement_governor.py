from __future__ import annotations

import json
from pathlib import Path

from .adaptive_policy import AdaptivePolicyStore
from .improvement_registry import ImprovementRegistry


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return default
        return json.loads(raw)
    except Exception:
        return default


def _save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


class ImprovementGovernor:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.registry = ImprovementRegistry(self.base_dir)
        self.policy = AdaptivePolicyStore(self.base_dir / "adaptive_policy.json")
        self.guard_state_path = self.base_dir / "self_healing_state.json"
        self.connectors_path = self.base_dir / "connectors.json"

    def _load_guard_state(self) -> dict:
        return _load_json(self.guard_state_path, {})

    def _save_guard_state(self, obj: dict) -> None:
        _save_json(self.guard_state_path, obj)

    def _load_connectors(self) -> dict:
        return _load_json(self.connectors_path, {"connectors": []})

    def _save_connectors(self, obj: dict) -> None:
        _save_json(self.connectors_path, obj)

    def apply_safe_improvements(self, planner_report: dict) -> dict:
        proposals = list(planner_report.get("items", []))
        self.registry.upsert_proposals(proposals)

        applied = []
        activated = []
        retired = []

        active_ids = {str(x.get("id") or "") for x in proposals if x.get("id")}
        self.registry.auto_retire_stale(active_ids=active_ids, stale_seconds=43200)

        policy = self.policy.dump()
        notes = list(((policy.get("notes") or [])))

        for item in proposals:
            pid = str(item.get("id") or "")
            if not pid:
                continue

            if pid == "eliminate_unknown_service_failures":
                guard = self._load_guard_state()
                if not bool(guard.get("force_specific_service_resolution", False)):
                    guard["force_specific_service_resolution"] = True
                    self._save_guard_state(guard)
                    self.registry.mark_applied(pid, "Enabled force_specific_service_resolution.")
                    applied.append(pid)
                self.registry.mark_active(pid, "Still relevant while unknown service failures exist.")
                activated.append(pid)

            elif pid == "anthropic_payload_compatibility":
                guard = self._load_guard_state()
                if not bool(guard.get("anthropic_safe_mode", False)):
                    guard["anthropic_safe_mode"] = True
                    self._save_guard_state(guard)
                    self.registry.mark_applied(pid, "Enabled anthropic_safe_mode.")
                    applied.append(pid)
                self.registry.mark_active(pid, "Still relevant while Claude 400 errors exist.")
                activated.append(pid)

            elif pid == "n8n_live_guarded_rollout":
                connectors = self._load_connectors()
                changed = False
                for c in connectors.get("connectors", []):
                    if str(c.get("connector_id") or "") == "n8n_primary":
                        meta = c.setdefault("metadata", {})
                        if meta.get("live_enabled", False):
                            meta["live_enabled"] = False
                            changed = True
                if changed:
                    self._save_connectors(connectors)
                    self.registry.mark_applied(pid, "Forced n8n live_enabled=false while rollout is guarded.")
                    applied.append(pid)
                self.registry.mark_active(pid, "Remains active until verify_api and dry-run webhook succeed.")
                activated.append(pid)

            elif pid == "active_snapshot_pressure":
                note = "Keep aggressive cleanup and archive completed missions earlier."
                if note not in notes:
                    notes.append(note)
                    self.policy.set_notes(notes)
                    self.policy.save()
                    self.registry.mark_applied(pid, "Added cleanup note to adaptive policy.")
                    applied.append(pid)
                self.registry.mark_active(pid, "Remains active while active snapshot count is elevated.")
                activated.append(pid)

            elif pid == "reduce_cloud_ambiguity":
                self.registry.mark_active(pid, "Tracked as active; requires runtime routing improvements, not just config.")
                activated.append(pid)

        return {
            "applied": applied,
            "active": activated,
            "retired": retired,
            "registry_count": len(self.registry.list_items()),
        }