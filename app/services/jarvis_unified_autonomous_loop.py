from __future__ import annotations

import json
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.jarvis_agent_roles_coordination import AgentRolesCoordinator
from app.services.jarvis_memory_learning_layer import JarvisMemoryLearningLayer
from app.services.jarvis_external_systems_readiness import JarvisExternalSystemsReadiness
from app.services.jarvis_explainability_operator_control import JarvisExplainabilityOperatorControl


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class LoopPhaseRecord:
    phase: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    details: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass
class UnifiedLoopResult:
    loop_run_id: str
    goal: str
    task: str
    status: str
    coordination_status: str
    recommended_action: str
    apply_lane_state: str
    mutation_outcome: str
    blocked_reasons: List[str] = field(default_factory=list)
    next_best_action: str = ""
    operator_message: str = ""
    subsystem_statuses: Dict[str, str] = field(default_factory=dict)
    phases: List[LoopPhaseRecord] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None


class JarvisUnifiedAutonomousLoop:
    """
    Integration Layer:
    - coordinates blocks 1..6 into a single guarded autonomous cycle
    - resilient to partial subsystem failures
    - emits unified operator-facing result
    """

    def __init__(
        self,
        project_root: str | Path,
        artifacts_root: Optional[str | Path] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "unified_autonomous_loop"
        )

        self.runs_dir = ensure_dir(self.artifacts_root / "runs")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")
        self.errors_dir = ensure_dir(self.artifacts_root / "errors")

        self.coordinator = AgentRolesCoordinator(project_root=self.project_root)
        self.memory_layer = JarvisMemoryLearningLayer(project_root=self.project_root)
        self.readiness_layer = JarvisExternalSystemsReadiness(project_root=self.project_root)
        self.explainability_layer = JarvisExplainabilityOperatorControl(project_root=self.project_root)

    # ------------------------------------------------------------------
    # utils
    # ------------------------------------------------------------------
    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "unified_autonomous_loop.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _phase_record(
        self,
        phase: str,
        started: datetime,
        status: str,
        details: Optional[Dict[str, Any]] = None,
        notes: Optional[List[str]] = None,
    ) -> LoopPhaseRecord:
        finished = datetime.now(timezone.utc)
        return LoopPhaseRecord(
            phase=phase,
            status=status,
            started_at=started.replace(microsecond=0).isoformat(),
            finished_at=finished.replace(microsecond=0).isoformat(),
            duration_seconds=round((finished - started).total_seconds(), 3),
            details=details or {},
            notes=notes or [],
        )

    def _capture_error(self, loop_run_id: str, phase: str, exc: Exception) -> Dict[str, Any]:
        payload = {
            "loop_run_id": loop_run_id,
            "phase": phase,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "created_at": utc_now_iso(),
        }
        self._write_json(self.errors_dir / f"{loop_run_id}_{phase}.json", payload)
        return payload

    # ------------------------------------------------------------------
    # unified loop
    # ------------------------------------------------------------------
    def run(self, goal: str, task: str, priority: str = "normal") -> UnifiedLoopResult:
        loop_run_id = "uloop_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self._log("info", f"loop started loop_run_id={loop_run_id}")

        result = UnifiedLoopResult(
            loop_run_id=loop_run_id,
            goal=goal,
            task=task,
            status="running",
            coordination_status="unknown",
            recommended_action="artifact_only",
            apply_lane_state="unknown",
            mutation_outcome="unknown",
        )

        subsystem_statuses: Dict[str, str] = {
            "coordination": "not_started",
            "memory_learning": "not_started",
            "external_readiness": "not_started",
            "explainability": "not_started",
        }
        artifacts: Dict[str, Any] = {}

        # --------------------------------------------------------------
        # Phase 1: Coordination
        # --------------------------------------------------------------
        try:
            started = datetime.now(timezone.utc)
            coord_run = self.coordinator.run(goal=goal, task=task, priority=priority)
            subsystem_statuses["coordination"] = "ok"
            result.coordination_status = coord_run.status
            result.recommended_action = coord_run.recommended_action
            result.mutation_outcome = coord_run.final_reason or coord_run.status
            artifacts["coordination"] = {
                "run_id": coord_run.run_id,
                "status": coord_run.status,
                "recommended_action": coord_run.recommended_action,
                "accepted_mutation_id": coord_run.accepted_mutation_id,
                "final_reason": coord_run.final_reason,
                "next_best_action": coord_run.next_best_action,
            }
            result.phases.append(
                self._phase_record(
                    phase="coordination",
                    started=started,
                    status="ok",
                    details=artifacts["coordination"],
                    notes=["Coordinator pipeline executed"],
                )
            )
        except Exception as exc:
            subsystem_statuses["coordination"] = "failed"
            err = self._capture_error(loop_run_id, "coordination", exc)
            result.phases.append(
                self._phase_record(
                    phase="coordination",
                    started=started,
                    status="failed",
                    details={"error": str(exc)},
                    notes=["Coordinator pipeline failed"],
                )
            )
            result.status = "failed"
            result.blocked_reasons.append(f"coordination_failed:{type(exc).__name__}")
            artifacts["coordination_error"] = err

        # --------------------------------------------------------------
        # Phase 2: External readiness
        # --------------------------------------------------------------
        try:
            started = datetime.now(timezone.utc)
            profiles = self.readiness_layer.assess_all()
            metrics = self.readiness_layer.collect_metrics()
            subsystem_statuses["external_readiness"] = "ok"
            ready_count = sum(1 for p in profiles if p.ready)
            not_ready_profiles = [p.system_name for p in profiles if not p.ready]
            artifacts["external_readiness"] = {
                "profiles_count": len(profiles),
                "ready_count": ready_count,
                "not_ready_systems": not_ready_profiles,
                "metrics": metrics,
            }
            result.phases.append(
                self._phase_record(
                    phase="external_readiness",
                    started=started,
                    status="ok",
                    details=artifacts["external_readiness"],
                    notes=["Readiness profiles refreshed"],
                )
            )
            if not_ready_profiles:
                result.blocked_reasons.extend([f"readiness_not_ready:{x}" for x in not_ready_profiles[:5]])
        except Exception as exc:
            subsystem_statuses["external_readiness"] = "failed"
            err = self._capture_error(loop_run_id, "external_readiness", exc)
            result.phases.append(
                self._phase_record(
                    phase="external_readiness",
                    started=started,
                    status="failed",
                    details={"error": str(exc)},
                    notes=["Readiness refresh failed"],
                )
            )
            artifacts["external_readiness_error"] = err
            result.blocked_reasons.append(f"external_readiness_failed:{type(exc).__name__}")

        # --------------------------------------------------------------
        # Phase 3: Memory / learning refresh
        # --------------------------------------------------------------
        try:
            started = datetime.now(timezone.utc)
            events = self.memory_layer.ingest_all()
            insights = self.memory_layer.build_module_insights(events)
            snapshot = self.memory_layer.build_learning_snapshot(events, insights)
            recommendation = self.memory_layer.recommend_next_best_action(snapshot)
            metrics = self.memory_layer.collect_metrics()

            subsystem_statuses["memory_learning"] = "ok"
            artifacts["memory_learning"] = {
                "events_count": len(events),
                "insights_count": len(insights),
                "snapshot_id": snapshot.snapshot_id,
                "recommendation": recommendation,
                "metrics": metrics,
            }
            result.phases.append(
                self._phase_record(
                    phase="memory_learning",
                    started=started,
                    status="ok",
                    details=artifacts["memory_learning"],
                    notes=["Learning state refreshed"],
                )
            )
        except Exception as exc:
            subsystem_statuses["memory_learning"] = "failed"
            err = self._capture_error(loop_run_id, "memory_learning", exc)
            result.phases.append(
                self._phase_record(
                    phase="memory_learning",
                    started=started,
                    status="failed",
                    details={"error": str(exc)},
                    notes=["Memory refresh failed"],
                )
            )
            artifacts["memory_learning_error"] = err
            result.blocked_reasons.append(f"memory_learning_failed:{type(exc).__name__}")

        # --------------------------------------------------------------
        # Phase 4: Explainability / operator summary
        # --------------------------------------------------------------
        try:
            started = datetime.now(timezone.utc)
            explanation = self.explainability_layer.build_explanation()
            control_snapshot = self.explainability_layer.build_control_snapshot(explanation)
            metrics = self.explainability_layer.collect_metrics()

            subsystem_statuses["explainability"] = "ok"
            artifacts["explainability"] = {
                "system_state": explanation.system_state,
                "summary": explanation.summary,
                "what_changed": explanation.what_changed,
                "why_not_applied": explanation.why_not_applied,
                "next_best_actions": explanation.next_best_actions,
                "current_mode": control_snapshot.current_mode,
                "overall_health": control_snapshot.overall_health,
                "apply_lane_state": control_snapshot.apply_lane_state,
                "operator_message": control_snapshot.operator_message,
                "metrics": metrics,
            }
            result.apply_lane_state = control_snapshot.apply_lane_state
            result.operator_message = control_snapshot.operator_message

            if explanation.next_best_actions:
                result.next_best_action = explanation.next_best_actions[0]
            elif artifacts.get("coordination", {}).get("next_best_action"):
                result.next_best_action = artifacts["coordination"]["next_best_action"]

            result.phases.append(
                self._phase_record(
                    phase="explainability",
                    started=started,
                    status="ok",
                    details=artifacts["explainability"],
                    notes=["Operator-facing summary refreshed"],
                )
            )
        except Exception as exc:
            subsystem_statuses["explainability"] = "failed"
            err = self._capture_error(loop_run_id, "explainability", exc)
            result.phases.append(
                self._phase_record(
                    phase="explainability",
                    started=started,
                    status="failed",
                    details={"error": str(exc)},
                    notes=["Explainability refresh failed"],
                )
            )
            artifacts["explainability_error"] = err
            result.blocked_reasons.append(f"explainability_failed:{type(exc).__name__}")

        # --------------------------------------------------------------
        # Final classification
        # --------------------------------------------------------------
        result.subsystem_statuses = subsystem_statuses
        result.artifacts = artifacts
        result.finished_at = utc_now_iso()

        if subsystem_statuses["coordination"] == "failed":
            result.status = "failed"
        else:
            if result.recommended_action == "apply" and result.coordination_status == "completed":
                result.status = "completed"
            elif result.recommended_action in {"artifact_only", "blocked"}:
                result.status = "degraded"
            else:
                result.status = "completed"

        if not result.next_best_action:
            if artifacts.get("memory_learning", {}).get("recommendation"):
                result.next_best_action = artifacts["memory_learning"]["recommendation"]
            elif artifacts.get("coordination", {}).get("next_best_action"):
                result.next_best_action = artifacts["coordination"]["next_best_action"]
            else:
                result.next_best_action = "Continue with cautious low-risk iterations and refresh operator summaries."

        # compact blocked reasons
        compact_reasons: List[str] = []
        for item in result.blocked_reasons:
            if item not in compact_reasons:
                compact_reasons.append(item)
        result.blocked_reasons = compact_reasons[:15]

        self._write_json(self.runs_dir / f"{loop_run_id}.json", self._result_payload(result))
        self._log(
            "info",
            f"loop finished loop_run_id={loop_run_id} status={result.status} action={result.recommended_action}"
        )
        return result

    def _result_payload(self, result: UnifiedLoopResult) -> Dict[str, Any]:
        return {
            "loop_run_id": result.loop_run_id,
            "goal": result.goal,
            "task": result.task,
            "status": result.status,
            "coordination_status": result.coordination_status,
            "recommended_action": result.recommended_action,
            "apply_lane_state": result.apply_lane_state,
            "mutation_outcome": result.mutation_outcome,
            "blocked_reasons": result.blocked_reasons,
            "next_best_action": result.next_best_action,
            "operator_message": result.operator_message,
            "subsystem_statuses": result.subsystem_statuses,
            "phases": [asdict(p) for p in result.phases],
            "artifacts": result.artifacts,
            "created_at": result.created_at,
            "finished_at": result.finished_at,
        }

    def collect_metrics(self) -> Dict[str, Any]:
        runs = []
        for path in sorted(self.runs_dir.glob("*.json")):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass

        completed = 0
        degraded = 0
        failed = 0
        apply_runs = 0

        subsystem_failures: Dict[str, int] = {}

        for run in runs:
            status = run.get("status")
            if status == "completed":
                completed += 1
            elif status == "degraded":
                degraded += 1
            elif status == "failed":
                failed += 1

            if run.get("recommended_action") == "apply":
                apply_runs += 1

            for name, sub_status in (run.get("subsystem_statuses") or {}).items():
                if sub_status != "ok":
                    subsystem_failures[name] = subsystem_failures.get(name, 0) + 1

        metrics = {
            "runs_count": len(runs),
            "completed_count": completed,
            "degraded_count": degraded,
            "failed_count": failed,
            "apply_runs": apply_runs,
            "subsystem_failures": subsystem_failures,
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics