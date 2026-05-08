from __future__ import annotations

import json
from pathlib import Path


class AutonomyControl:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.state_path = self.base_dir / "autonomous_improvement_state.json"
        self._ensure()

    def _ensure(self) -> None:
        if not self.state_path.exists():
            self.state_path.write_text(json.dumps({
                "enabled": True,
                "interval_seconds": 900,
                "last_tick_ts": 0.0,
                "observed_task_keys": [],
                "last_report": {},
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> dict:
        self._ensure()
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def enable(self, interval_seconds: int = 600) -> dict:
        data = self.load()
        data["enabled"] = True
        data["interval_seconds"] = int(max(120, interval_seconds))
        self.save(data)
        return {"status": "ok", "enabled": True, "interval_seconds": data["interval_seconds"]}

    def disable(self) -> dict:
        data = self.load()
        data["enabled"] = False
        self.save(data)
        return {"status": "ok", "enabled": False, "interval_seconds": data.get("interval_seconds", 900)}