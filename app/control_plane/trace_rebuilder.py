from __future__ import annotations

from pathlib import Path

from .execution_observer import ExecutionObserver


class TraceRebuilder:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.trace_path = self.base_dir / "service_trace.jsonl"
        self.telemetry_path = self.base_dir / "service_telemetry.json"
        self.observer = ExecutionObserver(self.base_dir)

    def rebuild(self) -> dict:
        if self.trace_path.exists():
            self.trace_path.unlink(missing_ok=True)
        if self.telemetry_path.exists():
            self.telemetry_path.unlink(missing_ok=True)

        state = {"observed_task_keys": []}
        observed = 0
        normalized = 0
        telemetry_added = 0
        traces_added = 0

        files = []
        for folder in ["active_snapshots", "archive_snapshots"]:
            d = self.base_dir / folder
            if d.exists():
                files.extend(list(d.glob("*.json")))
        files.sort(key=lambda p: p.stat().st_mtime)

        for path in files:
            row = self.observer.observe_snapshot_file(path, state)
            observed += int(row.get("observed", 0))
            normalized += int(row.get("normalized", 0))
            telemetry_added += int(row.get("telemetry_added", 0))
            traces_added += int(row.get("traces_added", 0))

        return {
            "status": "ok",
            "observed": observed,
            "normalized": normalized,
            "telemetry_added": telemetry_added,
            "traces_added": traces_added,
        }