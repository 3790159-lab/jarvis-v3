from __future__ import annotations

import json
import time
from pathlib import Path


class ImprovementJournal:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "improvement_journal.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, report: dict) -> None:
        row = {
            "ts": time.time(),
            "reason": report.get("reason", ""),
            "eventful": bool(report.get("eventful", False)),
            "observed_tasks": int(report.get("observed_tasks", 0) or 0),
            "normalized_tasks": int(report.get("normalized_tasks", 0) or 0),
            "telemetry_added": int(report.get("telemetry_added", 0) or 0),
            "traces_added": int(report.get("traces_added", 0) or 0),
            "mismatch_count_recent": int(report.get("mismatch_count_recent", 0) or 0),
            "planner_items": [x.get("id") for x in list((report.get("planner_report") or {}).get("items", []) or [])],
            "applied": list((report.get("governor_report") or {}).get("applied", []) or []),
            "active": list((report.get("governor_report") or {}).get("active", []) or []),
            "retired": list((report.get("governor_report") or {}).get("retired", []) or []),
            "healing_actions": list((report.get("healing_report") or {}).get("actions", []) or []),
            "n8n_configured": bool(((report.get("n8n_health") or {}).get("configured", False))),
            "n8n_verified": bool(((report.get("n8n_health") or {}).get("verified", False))),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-max(1, limit):]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out