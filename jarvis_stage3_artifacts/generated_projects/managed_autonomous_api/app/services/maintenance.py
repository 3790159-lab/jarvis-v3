from __future__ import annotations

from typing import Any, Dict, List

from app.core.time_utils import utc_now
from app.services.reliability import ReliabilityManager
from app.services.memory_store import MemoryStore
from app.services.observability import ObservabilityService


class MaintenanceService:
    def __init__(self) -> None:
        self.reliability = ReliabilityManager()
        self.memory = MemoryStore()
        self.observability = ObservabilityService()

    def normalize_legacy_runs(self) -> Dict[str, Any]:
        runs = self.reliability.list_runs()
        fixed = 0

        for run in runs:
            changed = False

            if run.get("status") == "created":
                stages = run.get("stages", [])
                if stages:
                    if any(s.get("status") == "running" for s in stages):
                        run["status"] = "running"
                    elif any(s.get("status") == "completed" for s in stages):
                        run["status"] = "running"
                    else:
                        run["status"] = "running"
                        stages[0]["status"] = "running"
                        stages[0]["updated_at"] = utc_now().isoformat()
                    run["updated_at"] = utc_now().isoformat()
                    changed = True

            if changed:
                self.reliability.append_run(run)
                fixed += 1

        return {
            "status": "ok",
            "fixed_runs": fixed
        }

    def sync_all(self) -> Dict[str, Any]:
        runs = self.reliability.list_runs()
        synced = 0
        for run in runs:
            self.memory.upsert_run_summary(run)
            synced += 1

        snapshot = self.observability.snapshot()

        return {
            "status": "ok",
            "synced_runs": synced,
            "snapshot_runs_total": snapshot["runs_total"]
        }

    def audit(self) -> Dict[str, Any]:
        runs = self.reliability.list_runs()
        memory_doc = self.memory.load()

        errors = []
        warnings = []

        memory_run_ids = set(memory_doc.get("runs", {}).keys())

        for run in runs:
            run_id = run.get("run_id")
            if run_id not in memory_run_ids:
                warnings.append(f"run missing in memory store: {run_id}")

            if run.get("status") == "created":
                warnings.append(f"legacy created status remains: {run_id}")

            if not run.get("stages"):
                errors.append(f"run has no stages: {run_id}")

        memory_validation = self.memory.validate_store()

        return {
            "ok": len(errors) == 0 and memory_validation["ok"],
            "errors": errors + memory_validation["errors"],
            "warnings": warnings + memory_validation["warnings"],
            "runs_count": len(runs),
            "memory_runs_count": len(memory_run_ids)
        }
