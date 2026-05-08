"""Phase 20 (Block D1): Mesh Settings — persistent mesh control preferences.

Stored in state/mesh_settings.json. Loaded lazily, defaults applied on missing keys.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).parent.parent.parent
_SETTINGS_PATH = ROOT / "state" / "mesh_settings.json"
_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

_DEFAULTS: Dict[str, Any] = {
    "mode": "auto",                    # "simple" | "auto" | "always"
    "confirm_before_mesh": "big_tasks",  # "always" | "big_tasks" | "never"
    "max_agents_per_task": 5,
    "cost_limit_per_task": 0.10,       # USD
    "cowork_delegation": "auto",       # "auto" | "confirm" | "never"
}


def load_mesh_settings() -> Dict[str, Any]:
    """Load settings, applying defaults for missing keys."""
    settings = dict(_DEFAULTS)
    if _SETTINGS_PATH.exists():
        try:
            saved = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            settings.update(saved)
        except Exception:
            pass
    return settings


def save_mesh_settings(settings: Dict[str, Any]) -> None:
    """Persist settings to disk."""
    _SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_setting(key: str, value: Any) -> Dict[str, Any]:
    """Update a single setting and save."""
    s = load_mesh_settings()
    s[key] = value
    save_mesh_settings(s)
    return s


def reset_to_defaults() -> Dict[str, Any]:
    """Reset all settings to defaults."""
    save_mesh_settings(dict(_DEFAULTS))
    return dict(_DEFAULTS)


def should_confirm_task(plan_agent_ids: list, estimated_cost_usd: float) -> bool:
    """Return True if the user should be asked to confirm before running this plan."""
    s = load_mesh_settings()
    confirm = s.get("confirm_before_mesh", "big_tasks")
    if confirm == "always":
        return True
    if confirm == "never":
        return False
    # "big_tasks": confirm if estimated_cost > 0.05 OR more than 2 agents
    return estimated_cost_usd > 0.05 or len(plan_agent_ids) > 2


def get_mode_label(mode: str) -> str:
    labels = {"simple": "🎯 SIMPLE", "auto": "🤖 AUTO", "always": "🚀 ALWAYS"}
    return labels.get(mode, mode)
