from __future__ import annotations

import json
import os
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

from app.services.jarvis_unified_autonomous_loop import JarvisUnifiedAutonomousLoop


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class NightIterationPlan:
    index: int
    goal: str
    task: str
    priority: str = "normal"


@dataclass
class NightIterationResult:
    index: int
    goal: str
    task: str
    status: str
    recommended_action: str
    apply_lane_state: str
    mutation_outcome: str
    blocked_reasons: List[str] = field(default_factory=list)
    next_best_action: str = ""
    operator_message: str = ""
    subsystem_statuses: Dict[str, str] = field(default_factory=dict)
    loop_run_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class NightSessionResult:
    session_id: str
    mode: str
    status: str
    iterations: List[NightIterationResult] = field(default_factory=list)
    completed_count: int = 0
    degraded_count: int = 0
    failed_count: int = 0
    apply_count: int = 0
    next_best_actions: List[str] = field(default_factory=list)
    operator_summary: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None


class JarvisUnifiedNightBridge:
    """
    New integrated night-mode execution layer built on top of Unified Autonomous Loop.
    Safe approach:
    - no mass regex patching legacy night mode
    - runs bounded iterations through unified loop
    - keeps full artifacts
    - supports degrade mode and operator summaries
    """

    def __init__(
        self,
        project_root: str | Path,
        artifacts_root: Optional[str | Path] = None,
        backend_base_url: str = "http://127.0.0.1:8015",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.backend_base_url = backend_base_url.rstrip("/")
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "unified_night_bridge"
        )

        self.sessions_dir = ensure_dir(self.artifacts_root / "sessions")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")
        self.errors_dir = ensure_dir(self.artifacts_root / "errors")

        self.loop = JarvisUnifiedAutonomousLoop(project_root=self.project_root)

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
        with (self.logs_dir / "unified_night_bridge.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _http_check(self, url: str, timeout: int = 15) -> Dict[str, Any]:
        try:
            with urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return {
                    "ok": 200 <= resp.status < 300,
                    "status": resp.status,
                    "body_excerpt": body[:300],
                }
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return {
                "ok": False,
                "error": str(exc),
            }

    def _capture_error(self, session_id: str, phase: str, exc: Exception) -> Dict[str, Any]:
        payload = {
            "session_id": session_id,
            "phase": phase,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "created_at": utc_now_iso(),
        }
        self._write_json(self.errors_dir / f"{session_id}_{phase}.json", payload)
        return payload

    # ------------------------------------------------------------------
    # planning
    # ------------------------------------------------------------------
    def default_iteration_plan(self) -> List[NightIterationPlan]:
        return [
            NightIterationPlan(
                index=1,
                goal="Improve backend stability and validation",
                task="Use unified autonomous loop to perform a safe low-risk backend improvement with validation and operator summary",
                priority="normal",
            ),
            NightIterationPlan(
                index=2,
                goal="Improve observability and failure diagnosis",
                task="Use unified autonomous loop to execute a guarded observability-oriented improvement and refresh explanation artifacts",
                priority="normal",
            ),
            NightIterationPlan(
                index=3,
                goal="Improve modular execution and coordination quality",
                task="Use unified autonomous loop to produce a safe coordination-oriented mutation and update memory snapshots",
                priority="normal",
            ),
            NightIterationPlan(
                index=4,
                goal="Prepare safer automation and external readiness",
                task="Use unified autonomous loop to refresh readiness, preserve safe apply lane, and produce operator-facing next actions",
                priority="normal",
            ),
        ]

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def run_session(
        self,
        mode: str = "safe_unified_night_mode",
        max_iterations: int = 4,
        degrade_on_failure: bool = True,
    ) -> NightSessionResult:
        session_id = "nightbridge_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self._log("info", f"session started session_id={session_id}")

        session = NightSessionResult(
            session_id=session_id,
            mode=mode,
            status="running",
        )

        # --------------------------------------------------------------
        # preflight backend health
        # --------------------------------------------------------------
        health = self._http_check(f"{self.backend_base_url}/health")
        if not health.get("ok"):
            session.status = "failed"
            session.operator_summary = "Backend health check failed before night session start."
            session.finished_at = utc_now_iso()
            self._write_json(self.sessions_dir / f"{session_id}.json", self._session_payload(session, {"preflight_health": health}))
            return session

        plans = self.default_iteration_plan()[:max_iterations]
        session_artifacts: Dict[str, Any] = {
            "preflight_health": health,
            "plans": [asdict(p) for p in plans],
        }

        for plan in plans:
            try:
                self._log("info", f"iteration start session_id={session_id} index={plan.index}")
                loop_result = self.loop.run(goal=plan.goal, task=plan.task, priority=plan.priority)

                iteration = NightIterationResult(
                    index=plan.index,
                    goal=plan.goal,
                    task=plan.task,
                    status=loop_result.status,
                    recommended_action=loop_result.recommended_action,
                    apply_lane_state=loop_result.apply_lane_state,
                    mutation_outcome=loop_result.mutation_outcome,
                    blocked_reasons=list(loop_result.blocked_reasons),
                    next_best_action=loop_result.next_best_action,
                    operator_message=loop_result.operator_message,
                    subsystem_statuses=dict(loop_result.subsystem_statuses),
                    loop_run_id=loop_result.loop_run_id,
                )
                session.iterations.append(iteration)

                if iteration.status == "completed":
                    session.completed_count += 1
                elif iteration.status == "degraded":
                    session.degraded_count += 1
                elif iteration.status == "failed":
                    session.failed_count += 1

                if iteration.recommended_action == "apply":
                    session.apply_count += 1

                if iteration.next_best_action:
                    session.next_best_actions.append(iteration.next_best_action)

                if loop_result.status == "failed" and not degrade_on_failure:
                    session.status = "failed"
                    break

            except Exception as exc:
                err = self._capture_error(session_id, f"iteration_{plan.index}", exc)
                self._log("error", f"iteration failed session_id={session_id} index={plan.index} error={exc}")
                session.failed_count += 1
                session.iterations.append(
                    NightIterationResult(
                        index=plan.index,
                        goal=plan.goal,
                        task=plan.task,
                        status="failed",
                        recommended_action="blocked",
                        apply_lane_state="restricted",
                        mutation_outcome="iteration_exception",
                        blocked_reasons=[f"iteration_exception:{type(exc).__name__}"],
                        next_best_action="Inspect saved error artifact and narrow the iteration scope.",
                        operator_message="Iteration failed before unified loop completion.",
                        subsystem_statuses={},
                        loop_run_id=None,
                    )
                )
                session_artifacts[f"iteration_{plan.index}_error"] = err
                if not degrade_on_failure:
                    session.status = "failed"
                    break

        # --------------------------------------------------------------
        # final classification
        # --------------------------------------------------------------
        if session.status != "failed":
            if session.failed_count > 0 and session.completed_count == 0:
                session.status = "degraded"
            elif session.failed_count > 0 or session.degraded_count > 0:
                session.status = "degraded"
            else:
                session.status = "completed"

        if session.completed_count > 0 and session.apply_count > 0:
            session.operator_summary = (
                "Night bridge completed successfully with safe apply-capable iterations and operator-facing summaries."
            )
        elif session.completed_count > 0:
            session.operator_summary = (
                "Night bridge completed, but iterations stayed mostly in cautious or artifact-oriented mode."
            )
        elif session.status == "degraded":
            session.operator_summary = (
                "Night bridge degraded: some iterations failed or were restricted, but artifacts and reasons were preserved."
            )
        else:
            session.operator_summary = (
                "Night bridge failed before producing a stable autonomous improvement cycle."
            )

        session.finished_at = utc_now_iso()

        # de-duplicate next best actions
        dedup_actions: List[str] = []
        for action in session.next_best_actions:
            if action and action not in dedup_actions:
                dedup_actions.append(action)
        session.next_best_actions = dedup_actions[:10]

        self._write_json(self.sessions_dir / f"{session_id}.json", self._session_payload(session, session_artifacts))
        self._log("info", f"session finished session_id={session_id} status={session.status}")
        return session

    def _session_payload(self, session: NightSessionResult, artifacts: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "mode": session.mode,
            "status": session.status,
            "iterations": [asdict(it) for it in session.iterations],
            "completed_count": session.completed_count,
            "degraded_count": session.degraded_count,
            "failed_count": session.failed_count,
            "apply_count": session.apply_count,
            "next_best_actions": session.next_best_actions,
            "operator_summary": session.operator_summary,
            "created_at": session.created_at,
            "finished_at": session.finished_at,
            "artifacts": artifacts,
        }

    def collect_metrics(self) -> Dict[str, Any]:
        sessions = []
        for path in sorted(self.sessions_dir.glob("*.json")):
            try:
                sessions.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass

        completed = 0
        degraded = 0
        failed = 0
        total_iterations = 0
        total_applies = 0

        for session in sessions:
            status = session.get("status")
            if status == "completed":
                completed += 1
            elif status == "degraded":
                degraded += 1
            elif status == "failed":
                failed += 1

            total_iterations += len(session.get("iterations", []) or [])
            total_applies += int(session.get("apply_count", 0))

        metrics = {
            "sessions_count": len(sessions),
            "completed_sessions": completed,
            "degraded_sessions": degraded,
            "failed_sessions": failed,
            "total_iterations": total_iterations,
            "total_applies": total_applies,
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics


def run_cli(project_root: str, max_iterations: int = 4, degrade_on_failure: bool = True) -> Dict[str, Any]:
    bridge = JarvisUnifiedNightBridge(project_root=project_root)
    session = bridge.run_session(
        max_iterations=max_iterations,
        degrade_on_failure=degrade_on_failure,
    )
    metrics = bridge.collect_metrics()
    payload = {
        "session": asdict(session),
        "metrics": metrics,
    }
    return payload