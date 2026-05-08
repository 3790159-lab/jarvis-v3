from __future__ import annotations

import json
from pathlib import Path


class ClaudePayloadProbe:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "claude_payload_profiles.json"
        self._ensure()

    def _ensure(self) -> None:
        if not self.path.exists():
            self.path.write_text(json.dumps({
                "active_profile": "safe_inline",
                "profiles": [
                    {
                        "id": "strict_system",
                        "description": "system field + user message",
                        "enabled": True,
                        "status": "candidate",
                    },
                    {
                        "id": "safe_inline",
                        "description": "system content inlined into user message",
                        "enabled": True,
                        "status": "active",
                    },
                    {
                        "id": "compact_inline",
                        "description": "short inline prompt with reduced payload size",
                        "enabled": True,
                        "status": "candidate",
                    },
                ],
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> dict:
        self._ensure()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def active_profile(self) -> str:
        data = self.load()
        return str(data.get("active_profile") or "safe_inline")

    def set_active_profile(self, profile_id: str) -> dict:
        data = self.load()
        found = False
        for item in data.get("profiles", []):
            if str(item.get("id")) == profile_id:
                item["status"] = "active"
                found = True
            elif item.get("status") == "active":
                item["status"] = "candidate"
        if found:
            data["active_profile"] = profile_id
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def health(self) -> dict:
        return self.load()