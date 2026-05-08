from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class OperatorExplanation:
    explanation_id: str
    system_state: str
    summary: str
    what_changed: List[str] = field(default_factory=list)
    why_not_applied: List[str] = field(default_factory=list)
    next_best_actions: List[str] = field(default_factory=list)
    risk_summary: Dict[str, Any] = field(default_factory=dict)
    readiness_summary: Dict[str, Any] = field(default_factory=dict)
    mutation_summary: Dict[str, Any] = field(default_factory=dict)
    coordination_summary: Dict[str, Any] = field(default_factory=dict)
    memory_summary: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class OperatorControlSnapshot:
    snapshot_id: str
    current_mode: str
    overall_health: str
    apply_lane_state: str
    blocked_reasons: List[str] = field(default_factory=list)
    recommended_focus: str = ""
    operator_message: str = ""
    created_at: str = field(default_factory=utc_now_iso)


class JarvisExplainabilityOperatorControl:
    """
    Block 6:
    - explain what changed
    - explain why something was not applied
    - unify signals from blocks 1..5
    - produce operator-facing summaries and control snapshots
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
            else self.project_root / "jarvis_stage3_artifacts" / "explainability_operator_control"
        )

        self.explanations_dir = ensure_dir(self.artifacts_root / "explanations")
        self.snapshots_dir = ensure_dir(self.artifacts_root / "snapshots")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

        self.mutation_root = self.project_root / "jarvis_stage3_artifacts" / "mutation_runtime"
        self.decision_root = self.project_root / "jarvis_stage3_artifacts" / "decision_risk_engine"
        self.coord_root = self.project_root / "jarvis_stage3_artifacts" / "agent_coordination"
        self.memory_root = self.project_root / "jarvis_stage3_artifacts" / "memory_learning"
        self.readiness_root = self.project_root / "jarvis_stage3_artifacts" / "external_systems_readiness"

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
        with (self.logs_dir / "explainability_operator_control.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _safe_read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _latest_json(self, directory: Path) -> Optional[Dict[str, Any]]:
        if not directory.exists():
            return None
        files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for file in files:
            data = self._safe_read_json(file)
            if data is not None:
                return data
        return None

    def _all_json(self, directory: Path) -> List[Dict[str, Any]]:
        if not directory.exists():
            return []
        items: List[Dict[str, Any]] = []
        for file in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            data = self._safe_read_json(file)
            if data is not None:
                items.append(data)
        return items

    # ------------------------------------------------------------------
    # summaries from prior blocks
    # ------------------------------------------------------------------
    def summarize_mutation_runtime(self) -> Dict[str, Any]:
        accepted = self._all_json(self.mutation_root / "accepted")
        rejected = self._all_json(self.mutation_root / "rejected")
        validation = self._all_json(self.mutation_root / "validation")

        what_changed: List[str] = []
        latest_reason = ""
        latest_outcome = "unknown"

        if accepted:
            latest = accepted[0]
            result = latest.get("result", {})
            latest_outcome = result.get("final_outcome", "accepted")
            latest_reason = result.get("reason", "")
            for path in result.get("touched_files", []) or []:
                what_changed.append(path)

        if not what_changed and rejected:
            latest = rejected[0]
            result = latest.get("result", {})
            latest_outcome = result.get("final_outcome", "rejected")
            latest_reason = result.get("reason", "")

        return {
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "validation_records_count": len(validation),
            "latest_outcome": latest_outcome,
            "latest_reason": latest_reason,
            "what_changed": sorted(set(what_changed)),
        }

    def summarize_decision_engine(self) -> Dict[str, Any]:
        outcomes = self._all_json(self.decision_root / "outcomes")
        if not outcomes:
            return {
                "latest_action": "unknown",
                "latest_reasons": [],
                "apply_count": 0,
                "artifact_only_count": 0,
                "blocked_count": 0,
            }

        latest = outcomes[0]
        apply_count = 0
        artifact_only_count = 0
        blocked_count = 0

        for item in outcomes:
            action = item.get("recommended_action")
            if action == "apply":
                apply_count += 1
            elif action == "artifact_only":
                artifact_only_count += 1
            elif action == "blocked":
                blocked_count += 1

        return {
            "latest_action": latest.get("recommended_action", "unknown"),
            "latest_reasons": latest.get("reasons", []) or [],
            "apply_count": apply_count,
            "artifact_only_count": artifact_only_count,
            "blocked_count": blocked_count,
            "latest_score": ((latest.get("scorecard") or {}).get("total_score")),
        }

    def summarize_coordination(self) -> Dict[str, Any]:
        runs = self._all_json(self.coord_root / "runs")
        if not runs:
            return {
                "latest_status": "unknown",
                "latest_recommended_action": "unknown",
                "latest_final_reason": "",
                "latest_next_best_action": "",
                "runs_count": 0,
            }

        latest = runs[0]
        return {
            "latest_status": latest.get("status", "unknown"),
            "latest_recommended_action": latest.get("recommended_action", "unknown"),
            "latest_final_reason": latest.get("final_reason", ""),
            "latest_next_best_action": latest.get("next_best_action", ""),
            "runs_count": len(runs),
            "latest_role_records_count": len(latest.get("role_records", []) or []),
            "latest_handoffs_count": len(latest.get("handoffs", []) or []),
        }

    def summarize_memory_learning(self) -> Dict[str, Any]:
        snapshots = self._all_json(self.memory_root / "snapshots")
        if not snapshots:
            return {
                "snapshots_count": 0,
                "latest_recommendation": "",
                "accepted_mutations": 0,
                "completed_runs": 0,
            }

        latest = snapshots[0]
        return {
            "snapshots_count": len(snapshots),
            "latest_recommendation": (latest.get("next_best_actions") or [""])[0] if (latest.get("next_best_actions") or []) else "",
            "accepted_mutations": latest.get("accepted_mutations", 0),
            "rejected_mutations": latest.get("rejected_mutations", 0),
            "completed_runs": latest.get("completed_runs", 0),
            "blocked_runs": latest.get("blocked_runs", 0),
            "top_success_modules": latest.get("top_success_modules", []) or [],
            "top_problem_modules": latest.get("top_problem_modules", []) or [],
        }

    def summarize_readiness(self) -> Dict[str, Any]:
        profiles = self._all_json(self.readiness_root / "profiles")
        if not profiles:
            return {
                "profiles_count": 0,
                "ready_count": 0,
                "not_ready_count": 0,
                "not_ready_systems": [],
            }

        ready = 0
        not_ready = 0
        not_ready_systems: List[str] = []
        next_actions: List[str] = []

        for p in profiles:
            if p.get("ready"):
                ready += 1
            else:
                not_ready += 1
                not_ready_systems.append(p.get("system_name", "unknown"))
                if p.get("next_best_action"):
                    next_actions.append(p.get("next_best_action"))

        return {
            "profiles_count": len(profiles),
            "ready_count": ready,
            "not_ready_count": not_ready,
            "not_ready_systems": not_ready_systems,
            "next_actions": next_actions[:10],
        }

    # ------------------------------------------------------------------
    # explainability
    # ------------------------------------------------------------------
    def build_explanation(self) -> OperatorExplanation:
        mutation = self.summarize_mutation_runtime()
        decision = self.summarize_decision_engine()
        coordination = self.summarize_coordination()
        memory = self.summarize_memory_learning()
        readiness = self.summarize_readiness()

        what_changed = list(mutation.get("what_changed", []) or [])
        why_not_applied: List[str] = []

        if decision.get("latest_action") == "artifact_only":
            why_not_applied.extend(decision.get("latest_reasons", []))
        elif decision.get("latest_action") == "blocked":
            why_not_applied.extend(decision.get("latest_reasons", []))

        if coordination.get("latest_recommended_action") == "artifact_only":
            if coordination.get("latest_final_reason"):
                why_not_applied.append(coordination["latest_final_reason"])

        if readiness.get("not_ready_systems"):
            why_not_applied.append(
                "Not all external systems are ready: " + ", ".join(readiness["not_ready_systems"])
            )

        next_best_actions: List[str] = []
        if coordination.get("latest_next_best_action"):
            next_best_actions.append(coordination["latest_next_best_action"])
        if memory.get("latest_recommendation"):
            next_best_actions.append(memory["latest_recommendation"])
        next_best_actions.extend(readiness.get("next_actions", []))
        next_best_actions = [x for x in next_best_actions if x]

        if mutation.get("accepted_count", 0) > 0 and decision.get("latest_action") == "apply":
            system_state = "healthy_apply_lane"
            summary = "Jarvis is operating in a healthy low-risk apply lane with successful recent mutations."
        elif decision.get("latest_action") in {"artifact_only", "blocked"}:
            system_state = "cautious_or_blocked"
            summary = "Jarvis is currently cautious or blocked for apply and is relying on safer decision paths."
        else:
            system_state = "observing"
            summary = "Jarvis is collecting signals and preparing operator-facing guidance."

        explanation = OperatorExplanation(
            explanation_id="exp_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            system_state=system_state,
            summary=summary,
            what_changed=what_changed,
            why_not_applied=why_not_applied[:10],
            next_best_actions=next_best_actions[:10],
            risk_summary=decision,
            readiness_summary=readiness,
            mutation_summary=mutation,
            coordination_summary=coordination,
            memory_summary=memory,
        )
        self._write_json(self.explanations_dir / f"{explanation.explanation_id}.json", asdict(explanation))
        self._log("info", f"build_explanation explanation_id={explanation.explanation_id} state={explanation.system_state}")
        return explanation

    def build_control_snapshot(self, explanation: OperatorExplanation) -> OperatorControlSnapshot:
        blocked_reasons = list(explanation.why_not_applied)

        if explanation.system_state == "healthy_apply_lane":
            current_mode = "apply_ready"
            overall_health = "good"
            apply_lane_state = "open"
            recommended_focus = "Expand successful low-risk mutation templates and operator summaries."
            operator_message = "System is healthy. Safe apply lane is open for controlled low-risk mutations."
        elif explanation.system_state == "cautious_or_blocked":
            current_mode = "guarded"
            overall_health = "mixed"
            apply_lane_state = "restricted"
            recommended_focus = "Resolve blocked reasons and improve readiness gaps before widening autonomy."
            operator_message = "System is stable, but apply lane is restricted. Use reasons and next actions to unblock safely."
        else:
            current_mode = "observe"
            overall_health = "stable"
            apply_lane_state = "limited"
            recommended_focus = "Gather more successful runs and improve operator-facing traces."
            operator_message = "System is observing and preparing safe next steps."

        snapshot = OperatorControlSnapshot(
            snapshot_id="opsnap_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            current_mode=current_mode,
            overall_health=overall_health,
            apply_lane_state=apply_lane_state,
            blocked_reasons=blocked_reasons[:10],
            recommended_focus=recommended_focus,
            operator_message=operator_message,
        )
        self._write_json(self.snapshots_dir / f"{snapshot.snapshot_id}.json", asdict(snapshot))
        self._log("info", f"build_control_snapshot snapshot_id={snapshot.snapshot_id} mode={snapshot.current_mode}")
        return snapshot

    def collect_metrics(self) -> Dict[str, Any]:
        metrics = {
            "explanations_count": len(list(self.explanations_dir.glob("*.json"))),
            "snapshots_count": len(list(self.snapshots_dir.glob("*.json"))),
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics