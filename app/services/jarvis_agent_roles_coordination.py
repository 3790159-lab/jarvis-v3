from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.services.jarvis_safe_mutation_foundation import SafeMutationFoundation
from app.services.jarvis_decision_risk_engine import JarvisDecisionRiskEngine


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class TaskEnvelope:
    run_id: str
    goal: str
    task: str
    mode: str = "safe_mutation"
    priority: str = "normal"
    constraints: List[str] = field(default_factory=list)
    target_paths: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class AgentHandoff:
    run_id: str
    from_role: str
    to_role: str
    status: str
    payload: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class RoleExecutionRecord:
    role: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    details: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass
class CoordinationRun:
    run_id: str
    envelope: TaskEnvelope
    status: str
    recommended_action: str
    role_records: List[RoleExecutionRecord] = field(default_factory=list)
    handoffs: List[AgentHandoff] = field(default_factory=list)
    accepted_mutation_id: Optional[str] = None
    final_reason: str = ""
    next_best_action: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    finished_at: Optional[str] = None


class CoordinationError(RuntimeError):
    pass


class AgentRolesCoordinator:
    """
    Block 3:
    - Supervisor: interpret goal and choose execution lane
    - Planner: derive low-risk mutation intent
    - Coder: create mutation candidate
    - Validator: state expected validation plan
    - RiskGuardian: invoke decision/risk engine
    - Archivist: persist summary and recommendation
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
            else self.project_root / "jarvis_stage3_artifacts" / "agent_coordination"
        )

        self.envelopes_dir = ensure_dir(self.artifacts_root / "envelopes")
        self.handoffs_dir = ensure_dir(self.artifacts_root / "handoffs")
        self.runs_dir = ensure_dir(self.artifacts_root / "runs")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")
        self.archivist_dir = ensure_dir(self.artifacts_root / "archivist")

        self.foundation = SafeMutationFoundation(project_root=self.project_root)
        self.risk_engine = JarvisDecisionRiskEngine(project_root=self.project_root)

    # ------------------------------------------------------------------
    # persistence / logging
    # ------------------------------------------------------------------
    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "agent_coordination.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------------
    # role helpers
    # ------------------------------------------------------------------
    def _record_role(
        self,
        role: str,
        started_at: datetime,
        status: str,
        details: Optional[Dict[str, Any]] = None,
        notes: Optional[List[str]] = None,
    ) -> RoleExecutionRecord:
        finished = datetime.now(timezone.utc)
        return RoleExecutionRecord(
            role=role,
            status=status,
            started_at=started_at.replace(microsecond=0).isoformat(),
            finished_at=finished.replace(microsecond=0).isoformat(),
            duration_seconds=round((finished - started_at).total_seconds(), 3),
            details=details or {},
            notes=notes or [],
        )

    def _handoff(
        self,
        run_id: str,
        from_role: str,
        to_role: str,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
        notes: Optional[List[str]] = None,
    ) -> AgentHandoff:
        handoff = AgentHandoff(
            run_id=run_id,
            from_role=from_role,
            to_role=to_role,
            status=status,
            payload=payload or {},
            notes=notes or [],
        )
        self._write_json(
            self.handoffs_dir / f"{run_id}_{from_role}_to_{to_role}_{uuid.uuid4().hex[:8]}.json",
            asdict(handoff),
        )
        return handoff

    # ------------------------------------------------------------------
    # roles
    # ------------------------------------------------------------------
    def supervisor(self, envelope: TaskEnvelope) -> Dict[str, Any]:
        """
        Decide whether the task is suitable for safe low-risk mutation.
        """
        goal = envelope.goal.lower()
        task = envelope.task.lower()

        low_risk_keywords = [
            "log",
            "logging",
            "health",
            "guard",
            "validation",
            "diagnosis",
            "observability",
            "helper",
            "safe",
        ]
        risky_keywords = [
            "migration",
            "rewrite",
            "major refactor",
            "delete module",
            "cross-service rewire",
        ]

        mode = "safe_mutation"
        reasons: List[str] = []
        allowed = True

        if any(k in task for k in risky_keywords) or any(k in goal for k in risky_keywords):
            allowed = False
            reasons.append("task_appears_high_risk")

        if any(k in task for k in low_risk_keywords) or any(k in goal for k in low_risk_keywords):
            reasons.append("task_matches_low_risk_lane")

        if not reasons:
            reasons.append("default_safe_lane_with_caution")

        return {
            "allowed": allowed,
            "mode": mode,
            "reasons": reasons,
            "priority": envelope.priority,
            "constraints": envelope.constraints,
        }

    def planner(self, envelope: TaskEnvelope, supervisor_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Produce a single-mutation low-risk plan.
        """
        safe_target = "jarvis_stage3_artifacts/temp/agent_coordination_probe.py"
        expected_effect = "Create or adjust a low-risk probe module for coordination testing"

        if envelope.target_paths:
            target_files = list(envelope.target_paths)
        else:
            target_files = [safe_target]

        plan = {
            "change_type": "small_code_patch",
            "risk_level": "low",
            "reversible": True,
            "estimated_validation_scope": "local_compile_and_health",
            "target_files": target_files,
            "expected_effect": expected_effect,
            "notes": [
                "single-file low-risk mutation",
                "designed for explicit validation and easy rollback",
            ],
        }
        return plan

    def coder(self, envelope: TaskEnvelope, plan: Dict[str, Any]) -> Any:
        """
        Convert plan into a real mutation candidate for SafeMutationFoundation.
        """
        first_target = plan["target_files"][0]
        create_content = (
            'def coordination_probe() -> str:\n'
            '    return "agent_coordination_ok"\n'
        )

        operations = [
            {
                "op": "create_file",
                "path": first_target,
                "content": create_content,
                "create_if_missing": True,
            }
        ]

        candidate = self.foundation.create_candidate(
            goal=envelope.goal,
            reason=envelope.task,
            change_type=plan["change_type"],
            target_files=plan["target_files"],
            expected_effect=plan["expected_effect"],
            risk_level=plan["risk_level"],
            reversible=plan["reversible"],
            estimated_validation_scope=plan["estimated_validation_scope"],
            operations=operations,
            metadata={
                "block": "agent_roles_coordination",
                "run_id": envelope.run_id,
                "mode": envelope.mode,
            },
        )
        return candidate

    def validator(self, candidate: Any) -> Dict[str, Any]:
        """
        Describe validation expectations before RiskGuardian decides.
        """
        target_files = getattr(candidate, "target_files", []) or []
        validation_plan = {
            "syntax_required": True,
            "compile_required": any(str(p).endswith(".py") for p in target_files),
            "health_required": True,
            "smoke_required": False,
            "target_files": list(target_files),
            "validation_scope": getattr(candidate, "estimated_validation_scope", "unknown"),
        }
        return validation_plan

    def risk_guardian(self, candidate: Any, validation_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Invoke Block 2 decision engine.
        """
        outcome = self.risk_engine.decide(candidate)
        return {
            "recommended_action": outcome.recommended_action,
            "allowed": outcome.allowed,
            "gate_status": outcome.gate_status,
            "summary": outcome.summary,
            "reasons": outcome.reasons,
            "score": outcome.scorecard.total_score,
            "scorecard": asdict(outcome.scorecard),
        }

    def archivist(self, run: CoordinationRun, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Persist orchestration summary and next best action.
        """
        payload = {
            "run_id": run.run_id,
            "status": run.status,
            "recommended_action": run.recommended_action,
            "accepted_mutation_id": run.accepted_mutation_id,
            "final_reason": run.final_reason,
            "next_best_action": run.next_best_action,
            "role_records_count": len(run.role_records),
            "handoffs_count": len(run.handoffs),
            "finished_at": run.finished_at,
            "extra": extra or {},
            "created_at": utc_now_iso(),
        }
        self._write_json(self.archivist_dir / f"{run.run_id}.json", payload)
        return payload

    # ------------------------------------------------------------------
    # orchestration
    # ------------------------------------------------------------------
    def run(self, goal: str, task: str, priority: str = "normal", target_paths: Optional[Sequence[str]] = None) -> CoordinationRun:
        run_id = "coord_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

        envelope = TaskEnvelope(
            run_id=run_id,
            goal=goal,
            task=task,
            priority=priority,
            target_paths=list(target_paths or []),
        )
        self._write_json(self.envelopes_dir / f"{run_id}.json", asdict(envelope))

        run = CoordinationRun(
            run_id=run_id,
            envelope=envelope,
            status="running",
            recommended_action="artifact_only",
        )

        self._log("info", f"coordination run started run_id={run_id}")

        # Supervisor
        started = datetime.now(timezone.utc)
        sup = self.supervisor(envelope)
        run.role_records.append(
            self._record_role(
                role="Supervisor",
                started_at=started,
                status="ok" if sup["allowed"] else "blocked",
                details=sup,
                notes=sup.get("reasons", []),
            )
        )

        if not sup["allowed"]:
            run.status = "blocked"
            run.recommended_action = "blocked"
            run.final_reason = "Supervisor blocked task before planning."
            run.next_best_action = "Narrow scope to a low-risk single-file mutation."
            run.finished_at = utc_now_iso()
            self.archivist(run, extra={"stage": "supervisor"})
            self._write_json(self.runs_dir / f"{run_id}.json", self._run_payload(run))
            return run

        run.handoffs.append(
            self._handoff(
                run_id=run_id,
                from_role="Supervisor",
                to_role="Planner",
                status="ok",
                payload=sup,
                notes=sup.get("reasons", []),
            )
        )

        # Planner
        started = datetime.now(timezone.utc)
        plan = self.planner(envelope, sup)
        run.role_records.append(
            self._record_role(
                role="Planner",
                started_at=started,
                status="ok",
                details=plan,
                notes=plan.get("notes", []),
            )
        )
        run.handoffs.append(
            self._handoff(
                run_id=run_id,
                from_role="Planner",
                to_role="Coder",
                status="ok",
                payload=plan,
                notes=plan.get("notes", []),
            )
        )

        # Coder
        started = datetime.now(timezone.utc)
        candidate = self.coder(envelope, plan)
        run.role_records.append(
            self._record_role(
                role="Coder",
                started_at=started,
                status="ok",
                details={"mutation_id": candidate.mutation_id, "target_files": candidate.target_files},
                notes=["candidate_created"],
            )
        )
        run.handoffs.append(
            self._handoff(
                run_id=run_id,
                from_role="Coder",
                to_role="Validator",
                status="ok",
                payload={"mutation_id": candidate.mutation_id, "target_files": candidate.target_files},
                notes=["handoff_candidate"],
            )
        )

        # Validator
        started = datetime.now(timezone.utc)
        validation_plan = self.validator(candidate)
        run.role_records.append(
            self._record_role(
                role="Validator",
                started_at=started,
                status="ok",
                details=validation_plan,
                notes=["validation_plan_created"],
            )
        )
        run.handoffs.append(
            self._handoff(
                run_id=run_id,
                from_role="Validator",
                to_role="RiskGuardian",
                status="ok",
                payload=validation_plan,
                notes=["validation_plan_ready"],
            )
        )

        # RiskGuardian
        started = datetime.now(timezone.utc)
        risk_result = self.risk_guardian(candidate, validation_plan)
        run.role_records.append(
            self._record_role(
                role="RiskGuardian",
                started_at=started,
                status="ok" if risk_result["recommended_action"] != "blocked" else "blocked",
                details=risk_result,
                notes=risk_result.get("reasons", []),
            )
        )

        run.recommended_action = risk_result["recommended_action"]

        if risk_result["recommended_action"] == "blocked":
            run.status = "blocked"
            run.final_reason = risk_result["summary"]
            run.next_best_action = "Reduce blast radius and improve validation coverage."
            run.finished_at = utc_now_iso()
            self.archivist(run, extra={"stage": "risk_guardian", "score": risk_result["score"]})
            self._write_json(self.runs_dir / f"{run_id}.json", self._run_payload(run))
            return run

        # For Block 3 we allow full execution only when RiskGuardian says apply.
        if risk_result["recommended_action"] == "artifact_only":
            run.status = "completed"
            run.final_reason = risk_result["summary"]
            run.next_best_action = "Improve confidence or rollback ease to cross apply threshold."
            run.finished_at = utc_now_iso()
            self.archivist(run, extra={"stage": "artifact_only", "score": risk_result["score"]})
            self._write_json(self.runs_dir / f"{run_id}.json", self._run_payload(run))
            return run

        run.handoffs.append(
            self._handoff(
                run_id=run_id,
                from_role="RiskGuardian",
                to_role="Archivist",
                status="apply",
                payload={"mutation_id": candidate.mutation_id, "score": risk_result["score"]},
                notes=["approved_for_apply"],
            )
        )

        # Execute mutation through Block 1 foundation
        mutation_result = self.foundation.execute_candidate(candidate)

        run.accepted_mutation_id = candidate.mutation_id
        run.status = "completed" if mutation_result.final_outcome == "accepted" else "degraded"
        run.final_reason = mutation_result.reason
        run.next_best_action = (
            "Proceed to richer orchestration cases."
            if mutation_result.final_outcome == "accepted"
            else "Investigate validation failures and narrow mutation scope."
        )
        run.finished_at = utc_now_iso()

        # Archivist
        started = datetime.now(timezone.utc)
        archive_payload = self.archivist(
            run,
            extra={
                "mutation_id": candidate.mutation_id,
                "mutation_final_outcome": mutation_result.final_outcome,
                "mutation_apply_status": mutation_result.apply_status,
                "mutation_validation_status": mutation_result.validation_status,
            },
        )
        run.role_records.append(
            self._record_role(
                role="Archivist",
                started_at=started,
                status="ok",
                details=archive_payload,
                notes=["summary_persisted"],
            )
        )

        self._write_json(self.runs_dir / f"{run_id}.json", self._run_payload(run))
        self._log(
            "info",
            f"coordination run finished run_id={run_id} action={run.recommended_action} status={run.status}"
        )
        return run

    def _run_payload(self, run: CoordinationRun) -> Dict[str, Any]:
        return {
            "run_id": run.run_id,
            "status": run.status,
            "recommended_action": run.recommended_action,
            "accepted_mutation_id": run.accepted_mutation_id,
            "final_reason": run.final_reason,
            "next_best_action": run.next_best_action,
            "created_at": run.created_at,
            "finished_at": run.finished_at,
            "envelope": asdict(run.envelope),
            "role_records": [asdict(r) for r in run.role_records],
            "handoffs": [asdict(h) for h in run.handoffs],
        }

    def collect_metrics(self) -> Dict[str, Any]:
        runs = list(self.runs_dir.glob("*.json"))

        completed = 0
        blocked = 0
        degraded = 0
        apply_runs = 0
        artifact_only_runs = 0

        for path in runs:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                status = data.get("status")
                action = data.get("recommended_action")
                if status == "completed":
                    completed += 1
                elif status == "blocked":
                    blocked += 1
                elif status == "degraded":
                    degraded += 1

                if action == "apply":
                    apply_runs += 1
                elif action == "artifact_only":
                    artifact_only_runs += 1
            except Exception:
                pass

        metrics = {
            "runs_count": len(runs),
            "completed_count": completed,
            "blocked_count": blocked,
            "degraded_count": degraded,
            "apply_runs": apply_runs,
            "artifact_only_runs": artifact_only_runs,
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics