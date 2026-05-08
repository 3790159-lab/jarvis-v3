from __future__ import annotations

import json
import time
from pathlib import Path


class ServiceTelemetryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data = {"services": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.save()
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {"services": {}}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, service: str, ok: bool, latency_ms: float, dry_run: bool, mode: str) -> None:
        self.load()
        row = self.data["services"].setdefault(service or "unknown", {
            "service": service or "unknown",
            "success_count": 0,
            "failure_count": 0,
            "live_success_count": 0,
            "dry_success_count": 0,
            "fallback_count": 0,
            "latency_ms_avg": 0.0,
            "samples": 0,
            "last_error": "",
            "last_success_ts": 0.0,
            "last_failure_ts": 0.0,
        })

        row["samples"] += 1
        n = row["samples"]
        row["latency_ms_avg"] = round(((row["latency_ms_avg"] * (n - 1)) + float(latency_ms or 0.0)) / max(1, n), 2)

        if ok:
            row["success_count"] += 1
            if dry_run:
                row["dry_success_count"] += 1
            else:
                row["live_success_count"] += 1
            row["last_success_ts"] = time.time()
        else:
            row["failure_count"] += 1
            row["last_failure_ts"] = time.time()

        if mode == "fallback_template":
            row["fallback_count"] += 1

        self.save()

    def set_last_error(self, service: str, error_text: str) -> None:
        self.load()
        row = self.data["services"].setdefault(service or "unknown", {"service": service or "unknown"})
        row["last_error"] = str(error_text or "")[:500]
        self.save()

    def summary(self) -> dict:
        self.load()
        rows = list(self.data.get("services", {}).values())
        rows.sort(key=lambda x: x.get("service", ""))
        return {
            "services": rows,
            "count": len(rows),
        }