from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any

from app.agents.schemas import AgentPolicy


BASE_DIR = Path(__file__).resolve().parents[2]
POLICY_FILE = BASE_DIR / "config" / "agent_policy_profiles.json"


def load_policy_profiles() -> Dict[str, Dict[str, Any]]:
    if not POLICY_FILE.exists():
        raise FileNotFoundError(f"Policy file not found: {POLICY_FILE}")
    return json.loads(POLICY_FILE.read_text(encoding="utf-8"))


def build_policy(profile_name: str) -> AgentPolicy:
    profiles = load_policy_profiles()
    if profile_name not in profiles:
        raise ValueError(f"Unknown policy profile: {profile_name}")
    data = dict(profiles[profile_name])
    data["profile"] = profile_name
    return AgentPolicy(**data)
