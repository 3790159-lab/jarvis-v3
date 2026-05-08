from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.core.time_utils import utc_now
from app.services.reliability import ReliabilityManager
from app.services.memory_store import MemoryStore


BASE_DIR = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = BASE_DIR / "artifacts"
METRICS_FILE = ARTIFACTS_DIR / "observability_metrics.json"


def _metrics_template() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "snapshot": {}
    }


def _read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
        return fallback
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        if not raw:
            path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
            return fallback
        data = json.loads(raw)
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class ObservabilityService:
    def __init__(self) -> None:
        self.path = METRICS_FILE
        self.reliability = ReliabilityManager()
        self.memory = MemoryStore()

    def load(self) -> Dict[str, Any]:
        return _read_json(self.path, _metrics_template())

    def save(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["updated_at"] = utc_now().isoformat()
        _write_json(self.path, doc)
        return doc

    def snapshot(self) -> Dict[str, Any]:
        runs = self.reliability.list_runs()
        memory_stats = self.memory.stats()

        statuses = {}
        stages_total = 0
        stages_completed = 0
        retries_total = 0
        quarantined_runs = 0
        legacy_created_runs = 0

        for run in runs:
            status = run.get("status", "unknown")
            statuses[status] = statuses.get(status, 0) + 1
            retries_total += run.get("retry_count", 0)

            if status == "quarantined":
                quarantined_runs += 1
            if status == "created":
                legacy_created_runs += 1

            for stage in run.get("stages", []):
                stages_total += 1
                if stage.get("status") == "completed":
                    stages_completed += 1

        snapshot = {
            "runs_total": len(runs),
            "run_statuses": statuses,
            "stages_total": stages_total,
            "stages_completed": stages_completed,
            "retries_total": retries_total,
            "quarantined_runs": quarantined_runs,
            "legacy_created_runs": legacy_created_runs,
            "completion_ratio": 0.0 if stages_total == 0 else round(stages_completed / stages_total, 4),
            "memory_runs_count": memory_stats["runs_count"],
            "memory_missions_count": memory_stats["missions_count"],
            "memory_notes_count": memory_stats["notes_count"],
            "generated_at": utc_now().isoformat()
        }

        doc = self.load()
        doc["snapshot"] = snapshot
        self.save(doc)
        return snapshot

    def timeline(self) -> Dict[str, Any]:
        runs = self.reliability.list_runs()
        ordered = sorted(
            runs,
            key=lambda x: x.get("updated_at") or x.get("created_at") or "",
            reverse=True
        )

        items = []
        for run in ordered[:20]:
            items.append({
                "run_id": run.get("run_id"),
                "mission_id": run.get("mission_id"),
                "task_id": run.get("task_id"),
                "status": run.get("status"),
                "retry_count": run.get("retry_count", 0),
                "updated_at": run.get("updated_at"),
                "objective": run.get("objective")
            })

        return {
            "count": len(items),
            "items": items
        }

    def system_summary(self) -> Dict[str, Any]:
        snapshot = self.snapshot()
        timeline = self.timeline()
        memory_validation = self.memory.validate_store()

        return {
            "status": "ok",
            "snapshot": snapshot,
            "timeline_count": timeline["count"],
            "memory_validation": memory_validation
        }
