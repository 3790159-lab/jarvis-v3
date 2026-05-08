from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mission_artifact_models import MissionArtifactRequest
from .mission_artifact_service import MissionArtifactService
from .runtime_bridge_models import (
    RuntimeBridgeGoalRecord,
    RuntimeBridgeMissionRecord,
    RuntimeBridgeSubmitRequest,
)


FINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class RuntimeBridgeService:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.root = self.project_root / "artifacts" / "runtime_bridge"
        self.goals_root = self.root / "goals"
        self.missions_root = self.root / "missions"
        self.indexes_root = self.root / "indexes"
        self.goals_root.mkdir(parents=True, exist_ok=True)
        self.missions_root.mkdir(parents=True, exist_ok=True)
        self.indexes_root.mkdir(parents=True, exist_ok=True)
        self.artifact_service = MissionArtifactService(project_root=self.project_root)

    def _goal_path(self, goal_id: str) -> Path:
        return self.goals_root / f"{goal_id}.json"

    def _mission_path(self, mission_id: str) -> Path:
        return self.missions_root / f"{mission_id}.json"

    def _index_path(self) -> Path:
        return self.indexes_root / "latest_runs.json"

    def _save_goal(self, goal: RuntimeBridgeGoalRecord) -> None:
        _write_json(self._goal_path(goal.goal_id), goal.model_dump())

    def _save_mission(self, mission: RuntimeBridgeMissionRecord) -> None:
        _write_json(self._mission_path(mission.mission_id), mission.model_dump())

    def _load_goal_record(self, goal_id: str) -> RuntimeBridgeGoalRecord:
        path = self._goal_path(goal_id)
        if not path.exists():
            raise ValueError(f"Goal not found: {goal_id}")
        return RuntimeBridgeGoalRecord.model_validate(_read_json(path))

    def _load_mission_record(self, mission_id: str) -> RuntimeBridgeMissionRecord:
        path = self._mission_path(mission_id)
        if not path.exists():
            raise ValueError(f"Mission not found: {mission_id}")
        return RuntimeBridgeMissionRecord.model_validate(_read_json(path))

    def _replace_index_entry(self, entry: dict[str, Any]) -> None:
        index_path = self._index_path()
        items: list[dict[str, Any]] = []
        if index_path.exists():
            try:
                existing = json.loads(index_path.read_text(encoding="utf-8"))
                if isinstance(existing, list):
                    items = existing
            except Exception:
                items = []

        mission_id = entry.get("mission_id")
        filtered = [x for x in items if x.get("mission_id") != mission_id]
        filtered.insert(0, entry)
        filtered = filtered[:100]
        _write_json(index_path, filtered)

    def _mission_index_entry(self, mission: RuntimeBridgeMissionRecord, goal: RuntimeBridgeGoalRecord) -> dict[str, Any]:
        execution = mission.execution or {}
        artifact_result = execution.get("artifact_result") or {}

        return {
            "goal_id": goal.goal_id,
            "mission_id": mission.mission_id,
            "status": mission.status,
            "mode": mission.mode,
            "objective": mission.objective,
            "selected_task_type": mission.selected_task_type,
            "artifact_name": mission.artifact_name,
            "planning_state": mission.planning_state,
            "execution_state": mission.execution_state,
            "can_execute": mission.can_execute,
            "can_cancel": mission.can_cancel,
            "created_at": mission.created_at,
            "updated_at": mission.updated_at,
            "run_id": execution.get("run_id"),
            "artifact_task_id": artifact_result.get("task_id"),
        }

    def get_goal(self, goal_id: str) -> dict[str, Any]:
        return self._load_goal_record(goal_id).model_dump()

    def get_mission(self, mission_id: str) -> dict[str, Any]:
        return self._load_mission_record(mission_id).model_dump()

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

    def _classify_mission(
        self,
        goal: RuntimeBridgeGoalRecord,
        mission: RuntimeBridgeMissionRecord,
        preferred_task_type: Any,
    ) -> tuple[RuntimeBridgeGoalRecord, RuntimeBridgeMissionRecord, dict[str, Any]]:
        mission_request = MissionArtifactRequest(
            objective=goal.objective,
            constraints=goal.constraints,
            payload=goal.payload,
            preferred_task_type=preferred_task_type,
        )
        classification = self.artifact_service.classify(mission_request)

        mission.updated_at = _now_iso()
        mission.selected_task_type = classification.get("selected_task_type")
        mission.artifact_name = classification.get("artifact_name")
        mission.classification = classification
        mission.planning_state = "classified"
        mission.summary = {
            "stage": "classified",
            "message": "Mission classified and prepared for execution.",
            "selected_task_type": classification.get("selected_task_type"),
            "artifact_name": classification.get("artifact_name"),
        }
        self._save_mission(mission)
        self._replace_index_entry(self._mission_index_entry(mission, goal))
        return goal, mission, classification

    def _execute_mission(
        self,
        goal: RuntimeBridgeGoalRecord,
        mission: RuntimeBridgeMissionRecord,
        preferred_task_type: Any,
    ) -> dict[str, Any]:
        if mission.status in FINAL_STATUSES:
            raise ValueError(f"Mission is already final: {mission.status}")
        if mission.status == "running":
            raise ValueError("Mission is already running")

        mission.status = "running"
        mission.execution_state = "running"
        mission.can_execute = False
        mission.can_cancel = True
        mission.updated_at = _now_iso()
        self._save_mission(mission)

        goal.status = "running"
        goal.updated_at = _now_iso()
        self._save_goal(goal)

        self._replace_index_entry(self._mission_index_entry(mission, goal))

        mission_request = MissionArtifactRequest(
            objective=goal.objective,
            constraints=goal.constraints,
            payload=goal.payload,
            preferred_task_type=preferred_task_type,
        )

        execution = self.artifact_service.execute(mission_request)

        mission.updated_at = _now_iso()
        mission.execution = execution
        mission.execution_state = "completed" if execution.get("ok") else "failed"
        mission.status = "completed" if execution.get("ok") else "failed"
        mission.can_execute = False
        mission.can_cancel = False
        mission.summary = {
            "stage": "executed",
            "message": "Mission executed through mission artifact service.",
            "selected_task_type": execution.get("selected_task_type"),
            "artifact_name": execution.get("artifact_name"),
            "artifact_ok": execution.get("ok"),
            "run_id": execution.get("run_id"),
            "artifact_output_dir": (execution.get("artifact_result") or {}).get("output_dir"),
            "artifact_manifest_path": (execution.get("artifact_result") or {}).get("manifest_path"),
        }
        mission.links.update({
            "mission_artifact_run_id": execution.get("run_id"),
            "mission_artifact_run_record_path": execution.get("run_record_path"),
            "artifact_task_id": (execution.get("artifact_result") or {}).get("task_id"),
            "artifact_manifest_path": (execution.get("artifact_result") or {}).get("manifest_path"),
            "artifact_output_dir": (execution.get("artifact_result") or {}).get("output_dir"),
        })
        self._save_mission(mission)

        goal.status = "completed" if execution.get("ok") else "failed"
        goal.updated_at = _now_iso()
        self._save_goal(goal)

        self._replace_index_entry(self._mission_index_entry(mission, goal))

        return {
            "ok": execution.get("ok", False),
            "goal_id": goal.goal_id,
            "mission_id": mission.mission_id,
            "status": mission.status,
            "goal_path": str(self._goal_path(goal.goal_id)),
            "mission_path": str(self._mission_path(mission.mission_id)),
            "classification": mission.classification,
            "execution": execution,
            "summary": mission.summary,
        }

    def submit(self, request: RuntimeBridgeSubmitRequest) -> dict[str, Any]:
        goal_id = f"goal_{uuid.uuid4().hex[:12]}"
        mission_id = f"mission_{uuid.uuid4().hex[:12]}"
        now = _now_iso()

        preferred_task_type = request.preferred_task_type

        goal = RuntimeBridgeGoalRecord(
            goal_id=goal_id,
            created_at=now,
            updated_at=now,
            objective=request.objective,
            constraints=request.constraints,
            payload=request.payload,
            preferred_task_type=preferred_task_type.value if preferred_task_type else None,
            auto_execute=request.auto_execute,
            latest_mission_id=mission_id,
            status="queued",
            metadata={
                "source": "runtime_bridge",
            },
        )
        self._save_goal(goal)

        mission = RuntimeBridgeMissionRecord(
            mission_id=mission_id,
            goal_id=goal_id,
            created_at=now,
            updated_at=now,
            status="queued",
            mode="auto_execute" if request.auto_execute else "planned_only",
            objective=request.objective,
            planning_state="created",
            execution_state="not_started",
            can_execute=True,
            can_cancel=True,
            summary={
                "stage": "created",
                "message": "Mission created and queued by runtime bridge.",
            },
            links={
                "goal_path": str(self._goal_path(goal_id)),
                "mission_path": str(self._mission_path(mission_id)),
            },
        )
        self._save_mission(mission)

        goal, mission, classification = self._classify_mission(goal, mission, preferred_task_type)

        if not request.auto_execute:
            mission.status = "planned"
            mission.execution_state = "not_started"
            mission.can_execute = True
            mission.can_cancel = True
            mission.updated_at = _now_iso()
            mission.summary = {
                "stage": "planned",
                "message": "Mission classified and stored for later execution.",
                "selected_task_type": classification.get("selected_task_type"),
                "artifact_name": classification.get("artifact_name"),
            }
            self._save_mission(mission)

            goal.status = "planned"
            goal.updated_at = _now_iso()
            self._save_goal(goal)

            self._replace_index_entry(self._mission_index_entry(mission, goal))

            return {
                "ok": True,
                "goal_id": goal_id,
                "mission_id": mission_id,
                "status": mission.status,
                "mode": "planned_only",
                "goal_path": str(self._goal_path(goal_id)),
                "mission_path": str(self._mission_path(mission_id)),
                "classification": classification,
                "summary": mission.summary,
            }

        return self._execute_mission(goal, mission, preferred_task_type)

    def execute_planned_mission(self, mission_id: str) -> dict[str, Any]:
        mission = self._load_mission_record(mission_id)
        goal = self._load_goal_record(mission.goal_id)

        if mission.status not in {"planned", "queued"}:
            raise ValueError(f"Mission cannot be executed from status: {mission.status}")

        preferred_task_type = None
        if goal.preferred_task_type:
            preferred_task_type = goal.preferred_task_type

        return self._execute_mission(goal, mission, preferred_task_type)

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        mission = self._load_mission_record(mission_id)
        goal = self._load_goal_record(mission.goal_id)

        if mission.status in FINAL_STATUSES:
            raise ValueError(f"Mission is already final: {mission.status}")
        if mission.status == "running":
            raise ValueError("Cannot cancel a currently running mission in this bridge version")

        mission.status = "cancelled"
        mission.execution_state = "cancelled"
        mission.can_execute = False
        mission.can_cancel = False
        mission.updated_at = _now_iso()
        mission.summary = {
            "stage": "cancelled",
            "message": "Mission cancelled before execution.",
            "selected_task_type": mission.selected_task_type,
            "artifact_name": mission.artifact_name,
        }
        self._save_mission(mission)

        goal.status = "cancelled"
        goal.updated_at = _now_iso()
        self._save_goal(goal)

        self._replace_index_entry(self._mission_index_entry(mission, goal))

        return {
            "ok": True,
            "goal_id": goal.goal_id,
            "mission_id": mission.mission_id,
            "status": mission.status,
            "goal_path": str(self._goal_path(goal.goal_id)),
            "mission_path": str(self._mission_path(mission.mission_id)),
            "summary": mission.summary,
        }
