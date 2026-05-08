from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class MemoryEvent:
    memory_id: str
    event_type: str
    source: str
    source_id: str
    status: str
    summary: str
    tags: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class ModuleInsight:
    module: str
    success_count: int = 0
    failure_count: int = 0
    blocked_count: int = 0
    recent_statuses: List[str] = field(default_factory=list)
    common_reasons: List[str] = field(default_factory=list)
    last_seen_at: Optional[str] = None


@dataclass
class LearningSnapshot:
    snapshot_id: str
    accepted_mutations: int
    rejected_mutations: int
    blocked_runs: int
    completed_runs: int
    degraded_runs: int
    top_success_modules: List[str] = field(default_factory=list)
    top_problem_modules: List[str] = field(default_factory=list)
    recurring_reasons: List[str] = field(default_factory=list)
    next_best_actions: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)


class JarvisMemoryLearningLayer:
    """
    Block 4:
    - ingest outcomes from mutation runtime / decision engine / agent coordination
    - produce structured memory events
    - summarize success/failure patterns
    - identify recurring reasons and weak modules
    - propose next best actions based on history
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
            else self.project_root / "jarvis_stage3_artifacts" / "memory_learning"
        )

        self.events_dir = ensure_dir(self.artifacts_root / "events")
        self.module_insights_dir = ensure_dir(self.artifacts_root / "module_insights")
        self.snapshots_dir = ensure_dir(self.artifacts_root / "snapshots")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

        self.mutation_runtime_root = self.project_root / "jarvis_stage3_artifacts" / "mutation_runtime"
        self.decision_root = self.project_root / "jarvis_stage3_artifacts" / "decision_risk_engine"
        self.coord_root = self.project_root / "jarvis_stage3_artifacts" / "agent_coordination"

    # ------------------------------------------------------------------
    # utils
    # ------------------------------------------------------------------
    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _read_json(self, path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "memory_learning.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _extract_module_names(self, files: Sequence[str]) -> List[str]:
        modules: List[str] = []
        for f in files:
            name = Path(f).name
            if name:
                modules.append(name)
        return sorted(set(modules))

    def _compact_reason(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "unspecified"
        text = re.sub(r"\s+", " ", text)
        return text[:180]

    # ------------------------------------------------------------------
    # ingestion
    # ------------------------------------------------------------------
    def ingest_mutation_results(self) -> List[MemoryEvent]:
        events: List[MemoryEvent] = []

        roots = [
            self.mutation_runtime_root / "accepted",
            self.mutation_runtime_root / "rejected",
        ]

        for root in roots:
            if not root.exists():
                continue

            for path in sorted(root.glob("*.json")):
                data = self._read_json(path)
                result = data.get("result", {})
                mutation_id = result.get("mutation_id", path.stem)
                status = result.get("final_outcome", result.get("status", "unknown"))
                touched_files = result.get("touched_files", []) or []
                checks = result.get("checks", []) or []

                event = MemoryEvent(
                    memory_id="mem_" + uuid.uuid4().hex[:10],
                    event_type="mutation_result",
                    source="mutation_runtime",
                    source_id=mutation_id,
                    status=status,
                    summary=f"Mutation {mutation_id} finished with outcome={status}",
                    tags=["mutation", status],
                    files=touched_files,
                    reasons=[self._compact_reason(result.get("reason", ""))],
                    metrics={
                        "checks_count": len(checks),
                        "touched_files_count": len(touched_files),
                        "apply_status": result.get("apply_status"),
                        "validation_status": result.get("validation_status"),
                        "rollback_status": result.get("rollback_status"),
                    },
                    recommendation=(
                        "Reuse this mutation style for similar low-risk changes."
                        if status == "accepted"
                        else "Review failure reason before reusing this pattern."
                    ),
                )
                events.append(event)

        return events

    def ingest_decision_outcomes(self) -> List[MemoryEvent]:
        events: List[MemoryEvent] = []
        outcomes_dir = self.decision_root / "outcomes"
        if not outcomes_dir.exists():
            return events

        for path in sorted(outcomes_dir.glob("*.json")):
            data = self._read_json(path)
            scorecard = data.get("scorecard", {})
            recommended_action = data.get("recommended_action", "unknown")
            mutation_id = data.get("mutation_id", path.stem)
            reasons = data.get("reasons", []) or []

            event = MemoryEvent(
                memory_id="mem_" + uuid.uuid4().hex[:10],
                event_type="decision_outcome",
                source="decision_risk_engine",
                source_id=mutation_id,
                status=recommended_action,
                summary=f"Decision engine classified {mutation_id} as {recommended_action}",
                tags=["decision", recommended_action],
                files=[],
                reasons=[self._compact_reason(r) for r in reasons],
                metrics={
                    "score": scorecard.get("total_score"),
                    "confidence": scorecard.get("confidence"),
                    "blast_radius": scorecard.get("blast_radius"),
                    "rollback_ease": scorecard.get("rollback_ease"),
                    "validation_coverage": scorecard.get("validation_coverage"),
                },
                recommendation=(
                    "Promote similar candidates into apply lane."
                    if recommended_action == "apply"
                    else "Increase safety evidence before apply."
                ),
            )
            events.append(event)

        return events

    def ingest_coordination_runs(self) -> List[MemoryEvent]:
        events: List[MemoryEvent] = []
        runs_dir = self.coord_root / "runs"
        if not runs_dir.exists():
            return events

        for path in sorted(runs_dir.glob("*.json")):
            data = self._read_json(path)
            run_id = data.get("run_id", path.stem)
            status = data.get("status", "unknown")
            envelope = data.get("envelope", {})
            final_reason = data.get("final_reason", "")
            next_best_action = data.get("next_best_action", "")

            files: List[str] = []
            for role_record in data.get("role_records", []) or []:
                details = role_record.get("details", {}) or {}
                if isinstance(details.get("target_files"), list):
                    files.extend(details["target_files"])

            event = MemoryEvent(
                memory_id="mem_" + uuid.uuid4().hex[:10],
                event_type="coordination_run",
                source="agent_coordination",
                source_id=run_id,
                status=status,
                summary=f"Coordination run {run_id} finished with status={status}",
                tags=["coordination", status],
                files=sorted(set(files)),
                reasons=[self._compact_reason(final_reason)] if final_reason else [],
                metrics={
                    "recommended_action": data.get("recommended_action"),
                    "role_records_count": len(data.get("role_records", []) or []),
                    "handoffs_count": len(data.get("handoffs", []) or []),
                },
                recommendation=next_best_action,
            )
            events.append(event)

        return events

    # ------------------------------------------------------------------
    # persistence / learning
    # ------------------------------------------------------------------
    def ingest_all(self) -> List[MemoryEvent]:
        events = []
        events.extend(self.ingest_mutation_results())
        events.extend(self.ingest_decision_outcomes())
        events.extend(self.ingest_coordination_runs())

        deduped: Dict[str, MemoryEvent] = {}
        for e in events:
            key = f"{e.event_type}:{e.source}:{e.source_id}:{e.status}"
            deduped[key] = e

        persisted = list(deduped.values())
        for event in persisted:
            self._write_json(self.events_dir / f"{event.memory_id}.json", asdict(event))

        self._log("info", f"ingest_all persisted_events={len(persisted)}")
        return persisted

    def build_module_insights(self, events: Sequence[MemoryEvent]) -> Dict[str, ModuleInsight]:
        insights: Dict[str, ModuleInsight] = {}

        for event in events:
            modules = self._extract_module_names(event.files)
            if not modules:
                continue

            for module in modules:
                if module not in insights:
                    insights[module] = ModuleInsight(module=module)

                item = insights[module]
                item.last_seen_at = event.created_at
                item.recent_statuses.append(event.status)
                item.recent_statuses = item.recent_statuses[-10:]

                for reason in event.reasons:
                    cr = self._compact_reason(reason)
                    if cr and cr not in item.common_reasons:
                        item.common_reasons.append(cr)
                item.common_reasons = item.common_reasons[:10]

                if event.status in {"accepted", "completed", "apply"}:
                    item.success_count += 1
                elif event.status in {"blocked", "rejected"}:
                    item.blocked_count += 1 if event.status == "blocked" else 0
                    item.failure_count += 1 if event.status == "rejected" else 0
                elif event.status in {"failed", "degraded"}:
                    item.failure_count += 1

        for module, insight in insights.items():
            self._write_json(self.module_insights_dir / f"{module}.json", asdict(insight))

        return insights

    def build_learning_snapshot(self, events: Sequence[MemoryEvent], insights: Dict[str, ModuleInsight]) -> LearningSnapshot:
        accepted_mutations = 0
        rejected_mutations = 0
        blocked_runs = 0
        completed_runs = 0
        degraded_runs = 0

        reason_counter: Dict[str, int] = {}
        recommendations: List[str] = []

        for event in events:
            if event.event_type == "mutation_result":
                if event.status == "accepted":
                    accepted_mutations += 1
                elif event.status == "rejected":
                    rejected_mutations += 1

            if event.event_type == "coordination_run":
                if event.status == "blocked":
                    blocked_runs += 1
                elif event.status == "completed":
                    completed_runs += 1
                elif event.status == "degraded":
                    degraded_runs += 1

            for reason in event.reasons:
                reason_counter[reason] = reason_counter.get(reason, 0) + 1

            if event.recommendation:
                recommendations.append(event.recommendation)

        success_modules = sorted(
            insights.values(),
            key=lambda x: (x.success_count, -x.failure_count),
            reverse=True,
        )
        problem_modules = sorted(
            insights.values(),
            key=lambda x: (x.failure_count + x.blocked_count, -x.success_count),
            reverse=True,
        )

        recurring_reasons = [
            k for k, _ in sorted(reason_counter.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ]
        next_best_actions = recommendations[:10]

        snapshot = LearningSnapshot(
            snapshot_id="learn_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            accepted_mutations=accepted_mutations,
            rejected_mutations=rejected_mutations,
            blocked_runs=blocked_runs,
            completed_runs=completed_runs,
            degraded_runs=degraded_runs,
            top_success_modules=[m.module for m in success_modules[:10]],
            top_problem_modules=[m.module for m in problem_modules[:10]],
            recurring_reasons=recurring_reasons,
            next_best_actions=next_best_actions,
        )
        self._write_json(self.snapshots_dir / f"{snapshot.snapshot_id}.json", asdict(snapshot))
        return snapshot

    def recommend_next_best_action(self, snapshot: LearningSnapshot) -> str:
        if snapshot.rejected_mutations > snapshot.accepted_mutations:
            return "Prefer narrower low-risk single-file mutations until acceptance improves."

        if snapshot.blocked_runs > 0:
            return "Reduce blast radius and improve validation coverage for blocked coordination tasks."

        if snapshot.completed_runs > 0 and snapshot.accepted_mutations > 0:
            return "Promote successful low-risk mutation patterns into recurring night mode templates."

        return "Continue collecting memory events and expand successful mutation archetypes."

    def collect_metrics(self) -> Dict[str, Any]:
        metrics = {
            "events_count": len(list(self.events_dir.glob("*.json"))),
            "module_insights_count": len(list(self.module_insights_dir.glob("*.json"))),
            "snapshots_count": len(list(self.snapshots_dir.glob("*.json"))),
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics