from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mission_artifact_models import MissionArtifactRequest
from .mission_artifact_service import MissionArtifactService
from .mission_graph_models import (
    GraphEventRecord,
    GraphMissionRecord,
    GraphMissionSubmitRequest,
)
from .mission_graph_packager import MissionGraphPackager
from .mission_graph_planner import build_graph_steps


FINAL_STEP_STATUSES = {"completed", "failed", "skipped", "cancelled"}
FINAL_MISSION_STATUSES = {"completed", "failed", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class MissionGraphService:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.root = self.project_root / "artifacts" / "mission_graph"
        self.missions_root = self.root / "missions"
        self.indexes_root = self.root / "indexes"
        self.locks_root = self.root / "locks"
        self.missions_root.mkdir(parents=True, exist_ok=True)
        self.indexes_root.mkdir(parents=True, exist_ok=True)
        self.locks_root.mkdir(parents=True, exist_ok=True)
        self.artifact_service = MissionArtifactService(project_root=self.project_root)
        self.packager = MissionGraphPackager(project_root=self.project_root)

    def _mission_path(self, graph_mission_id: str) -> Path:
        return self.missions_root / f"{graph_mission_id}.json"

    def _index_path(self) -> Path:
        return self.indexes_root / "latest_graph_runs.json"

    def _lock_path(self, graph_mission_id: str) -> Path:
        return self.locks_root / f"{graph_mission_id}.lock.json"

    def _save_mission(self, mission: GraphMissionRecord) -> None:
        _write_json(self._mission_path(mission.graph_mission_id), mission.model_dump())

    def _load_mission(self, graph_mission_id: str) -> GraphMissionRecord:
        path = self._mission_path(graph_mission_id)
        if not path.exists():
            raise ValueError(f"Graph mission not found: {graph_mission_id}")
        return GraphMissionRecord.model_validate(_read_json(path))

    def _append_event(self, mission: GraphMissionRecord, event: str, message: str = "", step_id: str | None = None, data: dict[str, Any] | None = None) -> None:
        mission.events.append(
            GraphEventRecord(
                ts=_now_iso(),
                event=event,
                step_id=step_id,
                message=message,
                data=data or {},
            )
        )
        mission.events = mission.events[-100:]

    def _replace_index_entry(self, mission: GraphMissionRecord) -> None:
        index_path = self._index_path()
        items: list[dict[str, Any]] = []
        if index_path.exists():
            try:
                raw = json.loads(index_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    items = raw
            except Exception:
                items = []

        completed = len([s for s in mission.steps if s.status == "completed"])
        failed = len([s for s in mission.steps if s.status == "failed"])
        skipped = len([s for s in mission.steps if s.status == "skipped"])
        ready = len([s for s in mission.steps if s.status == "ready"])
        pending = len([s for s in mission.steps if s.status == "pending"])
        running = len([s for s in mission.steps if s.status == "running"])
        cancelled = len([s for s in mission.steps if s.status == "cancelled"])

        entry = {
            "graph_mission_id": mission.graph_mission_id,
            "status": mission.status,
            "execution_state": mission.execution_state,
            "graph_kind": mission.graph_kind,
            "objective": mission.objective,
            "auto_execute": mission.auto_execute,
            "can_execute": mission.can_execute,
            "can_cancel": mission.can_cancel,
            "created_at": mission.created_at,
            "updated_at": mission.updated_at,
            "steps_total": len(mission.steps),
            "steps_completed": completed,
            "steps_failed": failed,
            "steps_skipped": skipped,
            "steps_ready": ready,
            "steps_pending": pending,
            "steps_running": running,
            "steps_cancelled": cancelled,
        }

        filtered = [x for x in items if x.get("graph_mission_id") != mission.graph_mission_id]
        filtered.insert(0, entry)
        filtered = filtered[:100]
        _write_json(index_path, filtered)

    def _refresh_ready_steps(self, mission: GraphMissionRecord) -> None:
        completed_ids = {s.step_id for s in mission.steps if s.status == "completed"}
        for step in mission.steps:
            if step.status != "pending":
                continue
            if all(dep in completed_ids for dep in step.depends_on):
                step.status = "ready"

    def _mark_descendants_skipped(self, mission: GraphMissionRecord, failed_step_id: str) -> None:
        changed = True
        failed_like = {failed_step_id}
        while changed:
            changed = False
            for step in mission.steps:
                if step.status in FINAL_STEP_STATUSES:
                    continue
                if any(dep in failed_like for dep in step.depends_on):
                    step.status = "skipped"
                    step.output = {
                        "reason": f"dependency failed or was skipped: {failed_step_id}"
                    }
                    self._append_event(
                        mission,
                        event="step_skipped",
                        step_id=step.step_id,
                        message="Step skipped due to failed dependency.",
                        data={"failed_dependency": failed_step_id},
                    )
                    failed_like.add(step.step_id)
                    changed = True

    def _recalculate_mission_status(self, mission: GraphMissionRecord) -> None:
        statuses = [s.status for s in mission.steps]
        if all(s == "completed" for s in statuses):
            mission.status = "completed"
        elif any(s == "failed" for s in statuses):
            mission.status = "failed"
        elif any(s == "running" for s in statuses):
            mission.status = "running"
        elif any(s == "ready" for s in statuses):
            mission.status = "ready"
        elif any(s == "pending" for s in statuses):
            mission.status = "planned"
        elif all(s in {"completed", "skipped"} for s in statuses):
            mission.status = "failed"
        elif all(s == "cancelled" for s in statuses):
            mission.status = "cancelled"
        else:
            mission.status = "planned"

        mission.summary = {
            "steps_total": len(mission.steps),
            "steps_completed": len([s for s in mission.steps if s.status == "completed"]),
            "steps_failed": len([s for s in mission.steps if s.status == "failed"]),
            "steps_skipped": len([s for s in mission.steps if s.status == "skipped"]),
            "steps_ready": len([s for s in mission.steps if s.status == "ready"]),
            "steps_pending": len([s for s in mission.steps if s.status == "pending"]),
            "steps_running": len([s for s in mission.steps if s.status == "running"]),
            "steps_cancelled": len([s for s in mission.steps if s.status == "cancelled"]),
        }

        if mission.status in FINAL_MISSION_STATUSES:
            mission.execution_state = "final"
            mission.can_execute = False
            mission.can_cancel = False
        elif mission.status in {"ready", "planned"}:
            mission.execution_state = "idle"
            mission.can_execute = True
            mission.can_cancel = True
        elif mission.status == "running":
            mission.execution_state = "running"
            mission.can_execute = False
            mission.can_cancel = False

    def _acquire_lock(self, graph_mission_id: str, action: str) -> None:
        lock_path = self._lock_path(graph_mission_id)
        if lock_path.exists():
            try:
                raw = _read_json(lock_path)
                existing_action = raw.get("action")
            except Exception:
                existing_action = "unknown"
            raise ValueError(f"Graph mission is locked by action: {existing_action}")

        _write_json(
            lock_path,
            {
                "graph_mission_id": graph_mission_id,
                "action": action,
                "locked_at": _now_iso(),
            },
        )

    def _release_lock(self, graph_mission_id: str) -> None:
        lock_path = self._lock_path(graph_mission_id)
        if lock_path.exists():
            lock_path.unlink()

    def _execute_step(self, mission: GraphMissionRecord, step_id: str) -> None:
        step = next((s for s in mission.steps if s.step_id == step_id), None)
        if step is None:
            raise ValueError(f"Step not found: {step_id}")
        if step.status not in {"ready"}:
            raise ValueError(f"Step is not ready for execution: {step.status}")

        if bool(step.constraints.get("force_fail")):
            step.status = "failed"
            step.attempts += 1
            step.output = {
                "error": "Forced failure for testing",
                "forced": True,
            }
            self._append_event(
                mission,
                event="step_failed",
                step_id=step.step_id,
                message=f"Step force-failed for testing: {step.title}",
                data={"forced": True},
            )
            self._mark_descendants_skipped(mission, step.step_id)
            mission.updated_at = _now_iso()
            self._refresh_ready_steps(mission)
            self._recalculate_mission_status(mission)
            self._save_mission(mission)
            self._replace_index_entry(mission)
            return

        step.status = "running"
        step.attempts += 1
        mission.updated_at = _now_iso()
        self._append_event(
            mission,
            event="step_started",
            step_id=step.step_id,
            message=f"Step started: {step.title}",
            data={"attempt": step.attempts, "task_type": step.task_type.value},
        )
        self._recalculate_mission_status(mission)
        self._save_mission(mission)
        self._replace_index_entry(mission)

        try:
            request = MissionArtifactRequest(
                objective=step.objective,
                constraints=step.constraints,
                payload=step.payload,
                preferred_task_type=step.task_type,
            )
            result = self.artifact_service.execute(request)

            if result.get("ok"):
                step.status = "completed"
                artifact_result = result.get("artifact_result") or {}
                step.output = {
                    "run_id": result.get("run_id"),
                    "run_record_path": result.get("run_record_path"),
                    "artifact_task_id": artifact_result.get("task_id"),
                    "artifact_manifest_path": artifact_result.get("manifest_path"),
                    "artifact_output_dir": artifact_result.get("output_dir"),
                    "artifact_name": result.get("artifact_name"),
                    "selected_task_type": result.get("selected_task_type"),
                }
                self._append_event(
                    mission,
                    event="step_completed",
                    step_id=step.step_id,
                    message=f"Step completed: {step.title}",
                    data={"run_id": result.get("run_id")},
                )
            else:
                step.status = "failed"
                step.output = {
                    "error": result,
                }
                self._append_event(
                    mission,
                    event="step_failed",
                    step_id=step.step_id,
                    message=f"Step failed: {step.title}",
                    data={"error": result},
                )
                self._mark_descendants_skipped(mission, step.step_id)

        except Exception as exc:
            step.status = "failed"
            step.output = {
                "error": str(exc),
            }
            self._append_event(
                mission,
                event="step_failed",
                step_id=step.step_id,
                message=f"Step failed with exception: {step.title}",
                data={"error": str(exc)},
            )
            self._mark_descendants_skipped(mission, step.step_id)

        mission.updated_at = _now_iso()
        self._refresh_ready_steps(mission)
        self._recalculate_mission_status(mission)
        self._save_mission(mission)
        self._replace_index_entry(mission)

    def submit(self, request: GraphMissionSubmitRequest) -> dict[str, Any]:
        graph_kind, steps = build_graph_steps(request)
        graph_mission_id = f"graph_{uuid.uuid4().hex[:12]}"
        now = _now_iso()

        mission = GraphMissionRecord(
            graph_mission_id=graph_mission_id,
            created_at=now,
            updated_at=now,
            objective=request.objective,
            graph_kind=graph_kind,
            auto_execute=request.auto_execute,
            status="ready" if any(s.status == "ready" for s in steps) else "planned",
            execution_state="idle",
            can_execute=True,
            can_cancel=True,
            steps=steps,
            summary={
                "steps_total": len(steps),
                "steps_completed": 0,
                "steps_failed": 0,
                "steps_skipped": 0,
                "steps_ready": len([s for s in steps if s.status == "ready"]),
                "steps_pending": len([s for s in steps if s.status == "pending"]),
                "steps_running": 0,
                "steps_cancelled": 0,
            },
            links={
                "mission_path": str(self._mission_path(graph_mission_id)),
            },
            events=[],
        )

        self._append_event(
            mission,
            event="graph_submitted",
            message="Graph mission submitted.",
            data={"graph_kind": graph_kind, "auto_execute": request.auto_execute},
        )

        self._save_mission(mission)
        self._replace_index_entry(mission)

        if request.auto_execute:
            return self.execute(graph_mission_id)

        return {
            "ok": True,
            "graph_mission_id": graph_mission_id,
            "status": mission.status,
            "graph_kind": graph_kind,
            "mission_path": str(self._mission_path(graph_mission_id)),
            "summary": mission.summary,
            "steps": [step.model_dump() for step in mission.steps],
        }

    def execute(self, graph_mission_id: str) -> dict[str, Any]:
        self._acquire_lock(graph_mission_id, "execute")
        try:
            mission = self._load_mission(graph_mission_id)

            if mission.status in FINAL_MISSION_STATUSES:
                raise ValueError(f"Graph mission already final: {mission.status}")
            if mission.execution_state == "running":
                raise ValueError("Graph mission is already running")

            self._append_event(
                mission,
                event="graph_execute_started",
                message="Graph execution started.",
            )
            mission.execution_state = "running"
            mission.can_execute = False
            mission.can_cancel = False
            mission.updated_at = _now_iso()
            self._save_mission(mission)
            self._replace_index_entry(mission)

            while True:
                self._refresh_ready_steps(mission)
                ready_steps = [s for s in mission.steps if s.status == "ready"]
                if not ready_steps:
                    break

                for step in ready_steps:
                    self._execute_step(mission, step.step_id)
                    if mission.status == "failed":
                        break

                if mission.status == "failed":
                    break

            mission.updated_at = _now_iso()
            self._recalculate_mission_status(mission)
            self._append_event(
                mission,
                event="graph_execute_finished",
                message="Graph execution finished.",
                data={"status": mission.status},
            )
            self._save_mission(mission)
            self._replace_index_entry(mission)

            return {
                "ok": mission.status == "completed",
                "graph_mission_id": mission.graph_mission_id,
                "status": mission.status,
                "graph_kind": mission.graph_kind,
                "mission_path": str(self._mission_path(mission.graph_mission_id)),
                "summary": mission.summary,
                "steps": [step.model_dump() for step in mission.steps],
                "events_tail": [e.model_dump() for e in mission.events[-10:]],
            }
        finally:
            self._release_lock(graph_mission_id)

    def retry_step(self, graph_mission_id: str, step_id: str) -> dict[str, Any]:
        self._acquire_lock(graph_mission_id, "retry_step")
        try:
            mission = self._load_mission(graph_mission_id)

            if mission.execution_state == "running":
                raise ValueError("Cannot retry while graph mission is running")

            step = next((s for s in mission.steps if s.step_id == step_id), None)
            if step is None:
                raise ValueError(f"Step not found: {step_id}")
            if step.status != "failed":
                raise ValueError(f"Only failed steps can be retried. Current status: {step.status}")

            dep_statuses = {dep: next((s.status for s in mission.steps if s.step_id == dep), None) for dep in step.depends_on}
            if any(status != "completed" for status in dep_statuses.values()):
                raise ValueError("Cannot retry step because dependencies are not completed")

            step.status = "ready"
            step.output = {}
            step.constraints.pop("force_fail", None)

            self._append_event(
                mission,
                event="step_retry_prepared",
                step_id=step_id,
                message="Failed step reset to ready for retry.",
            )

            for downstream in mission.steps:
                if step_id in downstream.depends_on and downstream.status == "skipped":
                    downstream.status = "pending"
                    downstream.output = {}
                    self._append_event(
                        mission,
                        event="downstream_reset",
                        step_id=downstream.step_id,
                        message="Downstream skipped step reset to pending after retry preparation.",
                        data={"upstream_step_id": step_id},
                    )

            mission.updated_at = _now_iso()
            self._refresh_ready_steps(mission)
            self._recalculate_mission_status(mission)
            self._save_mission(mission)
            self._replace_index_entry(mission)

            self._release_lock(graph_mission_id)
            return self.execute(graph_mission_id)
        except Exception:
            self._release_lock(graph_mission_id)
            raise

    def rerun(self, graph_mission_id: str) -> dict[str, Any]:
        self._acquire_lock(graph_mission_id, "rerun")
        try:
            mission = self._load_mission(graph_mission_id)

            if mission.execution_state == "running":
                raise ValueError("Cannot rerun while graph mission is running")
            if mission.status == "cancelled":
                raise ValueError("Cannot rerun a cancelled graph mission in this version")

            changed = False

            for step in mission.steps:
                if step.status in {"failed", "skipped"}:
                    step.status = "pending" if step.depends_on else "ready"
                    step.output = {}
                    changed = True

            self._refresh_ready_steps(mission)
            self._append_event(
                mission,
                event="graph_rerun_prepared",
                message="Graph rerun prepared for incomplete steps.",
            )
            mission.updated_at = _now_iso()
            self._recalculate_mission_status(mission)
            self._save_mission(mission)
            self._replace_index_entry(mission)

            if not changed and mission.status == "completed":
                return {
                    "ok": True,
                    "graph_mission_id": mission.graph_mission_id,
                    "status": mission.status,
                    "graph_kind": mission.graph_kind,
                    "mission_path": str(self._mission_path(mission.graph_mission_id)),
                    "summary": mission.summary,
                    "steps": [step.model_dump() for step in mission.steps],
                    "message": "Nothing to rerun. Graph is already completed.",
                }

            self._release_lock(graph_mission_id)
            return self.execute(graph_mission_id)
        except Exception:
            self._release_lock(graph_mission_id)
            raise

    def cancel(self, graph_mission_id: str) -> dict[str, Any]:
        self._acquire_lock(graph_mission_id, "cancel")
        try:
            mission = self._load_mission(graph_mission_id)

            if mission.status in FINAL_MISSION_STATUSES:
                raise ValueError(f"Graph mission already final: {mission.status}")
            if mission.execution_state == "running":
                raise ValueError("Cannot cancel a running graph mission in this version")

            mission.status = "cancelled"
            mission.execution_state = "final"
            mission.can_execute = False
            mission.can_cancel = False

            for step in mission.steps:
                if step.status in {"pending", "ready"}:
                    step.status = "cancelled"
                    step.output = {"reason": "graph_cancelled"}

            self._append_event(
                mission,
                event="graph_cancelled",
                message="Graph mission cancelled before execution.",
            )
            mission.updated_at = _now_iso()
            self._recalculate_mission_status(mission)
            mission.status = "cancelled"
            mission.execution_state = "final"
            mission.can_execute = False
            mission.can_cancel = False
            self._save_mission(mission)
            self._replace_index_entry(mission)

            return {
                "ok": True,
                "graph_mission_id": mission.graph_mission_id,
                "status": mission.status,
                "graph_kind": mission.graph_kind,
                "mission_path": str(self._mission_path(mission.graph_mission_id)),
                "summary": mission.summary,
                "steps": [step.model_dump() for step in mission.steps],
                "events_tail": [e.model_dump() for e in mission.events[-10:]],
            }
        finally:
            self._release_lock(graph_mission_id)

    def package(self, graph_mission_id: str) -> dict[str, Any]:
        mission = self._load_mission(graph_mission_id)
        if mission.status != "completed":
            raise ValueError("Only completed graph missions can be packaged")

        result = self.packager.package(mission)
        self._append_event(
            mission,
            event="graph_packaged",
            message="Graph mission packaged successfully.",
            data={"bundle_dir": result.get("bundle_dir")},
        )
        mission.links["bundle_manifest_path"] = result.get("bundle_manifest_path")
        mission.links["bundle_summary_path"] = result.get("bundle_summary_path")
        mission.links["step_outputs_path"] = result.get("step_outputs_path")
        mission.updated_at = _now_iso()
        self._save_mission(mission)
        self._replace_index_entry(mission)

        return result

    def create_failure_test_graph(self) -> dict[str, Any]:
        request = GraphMissionSubmitRequest(
            objective="Failure injection graph for retry validation",
            graph_kind="content_bundle",
            auto_execute=False,
            constraints={
                "name": "Failure Test Pack",
                "niche": "AI testing",
                "audience": "builders",
                "tone": "technical",
                "table_name": "Failure Test Calendar",
            },
            payload={},
        )
        result = self.submit(request)
        mission = self._load_mission(result["graph_mission_id"])

        for step in mission.steps:
            if step.step_id == "step_content_table":
                step.constraints["force_fail"] = True

        self._append_event(
            mission,
            event="failure_injection_prepared",
            message="Forced failure enabled for step_content_table.",
            step_id="step_content_table",
            data={"force_fail": True},
        )
        mission.updated_at = _now_iso()
        self._save_mission(mission)
        self._replace_index_entry(mission)

        return {
            "ok": True,
            "graph_mission_id": mission.graph_mission_id,
            "status": mission.status,
            "graph_kind": mission.graph_kind,
            "mission_path": str(self._mission_path(mission.graph_mission_id)),
            "prepared_failure_step_id": "step_content_table",
        }

    def get_mission(self, graph_mission_id: str) -> dict[str, Any]:
        return self._load_mission(graph_mission_id).model_dump()

    def list_runs(self) -> dict[str, Any]:
        index_path = self._index_path()
        items: list[dict[str, Any]] = []
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    items = data
            except Exception:
                items = []
        return {
            "count": len(items),
            "items": items,
        }
