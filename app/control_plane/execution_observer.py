from __future__ import annotations

import json
from pathlib import Path

from .service_resolver import ServiceResolver
from .service_trace import ServiceTraceStore
from .service_telemetry import ServiceTelemetryStore
from .text_normalizer import normalize_snapshot_dict


class ExecutionObserver:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.trace = ServiceTraceStore(self.base_dir / "service_trace.jsonl")
        self.telemetry = ServiceTelemetryStore(self.base_dir / "service_telemetry.json")
        self.resolver = ServiceResolver(self.base_dir)

    def observe_snapshot_file(self, snapshot_path: Path, state: dict) -> dict:
        if not snapshot_path.exists():
            return {"observed": 0, "normalized": 0, "telemetry_added": 0, "traces_added": 0}

        raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot, norm = normalize_snapshot_dict(raw)
        if snapshot != raw:
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

        observed = 0
        telemetry_added = 0
        traces_added = 0

        observed_keys = set(state.get("observed_task_keys", []))
        mission_id = str(snapshot.get("mission_id") or snapshot_path.stem)

        for task_entry in list(snapshot.get("tasks") or []):
            task = task_entry.get("task") or {}
            result = task_entry.get("result") or {}
            task_id = str(task.get("task_id") or "")
            attempts = int(task_entry.get("attempts") or 0)
            key = f"{mission_id}:{task_id}:{attempts}"
            if key in observed_keys:
                continue

            actual_service = self.resolver.resolve_actual_service(
                agent_id=str(task_entry.get("assigned_agent_id") or result.get("agent_id") or ""),
                capability=str(task.get("required_capability") or ""),
                task_type=str(task.get("task_type") or ""),
                provider=str(task_entry.get("provider") or ""),
                preferred_service=str((task.get("metadata") or {}).get("preferred_service") or ""),
                handoff_notes=result.get("handoff_notes") or [],
            )
            desired_service = str((task.get("metadata") or {}).get("preferred_service") or "")
            dry_run = bool((task.get("metadata") or {}).get("dry_run", False))
            duration_ms = float(((result.get("metrics") or {}).get("duration_ms")) or 0.0)
            status = str(task_entry.get("status") or "").lower()
            ok = status == "completed"
            guidance_domains = list((((task.get("metadata") or {}).get("learning_guidance") or {}).get("domain_paths")) or [])

            self.trace.append(
                task_id=task_id,
                agent_id=str(task_entry.get("assigned_agent_id") or result.get("agent_id") or ""),
                capability=str(task.get("required_capability") or ""),
                task_type=str(task.get("task_type") or ""),
                planned_provider=str(task_entry.get("provider") or ""),
                desired_service=desired_service,
                attempted_service=actual_service,
                actual_service=actual_service,
                status=("success" if ok else "failure"),
                reason="backfill_from_snapshot_v2",
                dry_run=dry_run,
                guidance_domains=guidance_domains,
            )
            self.telemetry.record(
                service=actual_service or "unknown",
                ok=ok,
                latency_ms=duration_ms,
                dry_run=dry_run,
                mode="backfill",
            )

            traces_added += 1
            telemetry_added += 1
            observed += 1
            observed_keys.add(key)

        state["observed_task_keys"] = list(observed_keys)[-8000:]
        return {
            "observed": observed,
            "normalized": norm.get("fixed_tasks", 0),
            "telemetry_added": telemetry_added,
            "traces_added": traces_added,
        }