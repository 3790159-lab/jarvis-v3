from __future__ import annotations

import json
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
class RiskSignal:
    name: str
    value: float
    weight: float
    reason: str


@dataclass
class DecisionScorecard:
    mutation_id: str
    total_score: float
    normalized_score: float
    confidence: float
    blast_radius: float
    rollback_ease: float
    validation_coverage: float
    risk_score: float
    touched_files_count: int
    signals: List[RiskSignal] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    recommended_action: str = "artifact_only"
    assessed_at: str = field(default_factory=utc_now_iso)


@dataclass
class DecisionOutcome:
    mutation_id: str
    allowed: bool
    recommended_action: str
    gate_status: str
    summary: str
    reasons: List[str]
    scorecard: DecisionScorecard
    created_at: str = field(default_factory=utc_now_iso)


class JarvisDecisionRiskEngine:
    """
    Block 2:
    - computes multi-factor score
    - estimates confidence / blast radius / rollback ease / validation coverage
    - decides apply / artifact_only / blocked
    """

    def __init__(
        self,
        project_root: str | Path,
        artifacts_root: Optional[str | Path] = None,
        allow_apply_threshold: float = 72.0,
        artifact_only_threshold: float = 45.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "decision_risk_engine"
        )
        self.allow_apply_threshold = allow_apply_threshold
        self.artifact_only_threshold = artifact_only_threshold

        self.scorecards_dir = ensure_dir(self.artifacts_root / "scorecards")
        self.outcomes_dir = ensure_dir(self.artifacts_root / "outcomes")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "decision_risk_engine.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _normalize_candidate(self, candidate: Any) -> Dict[str, Any]:
        if hasattr(candidate, "__dataclass_fields__"):
            data = asdict(candidate)
        elif isinstance(candidate, dict):
            data = dict(candidate)
        else:
            raise TypeError(f"Unsupported candidate type: {type(candidate)!r}")

        data.setdefault("mutation_id", "mut_" + uuid.uuid4().hex[:10])
        data.setdefault("goal", "")
        data.setdefault("reason", "")
        data.setdefault("change_type", "")
        data.setdefault("target_files", [])
        data.setdefault("expected_effect", "")
        data.setdefault("risk_level", "medium")
        data.setdefault("reversible", False)
        data.setdefault("estimated_validation_scope", "local_only")
        data.setdefault("operations", [])
        data.setdefault("metadata", {})
        return data

    def _risk_level_numeric(self, risk_level: str) -> float:
        mapping = {
            "very_low": 10.0,
            "low": 20.0,
            "medium": 50.0,
            "high": 80.0,
            "critical": 95.0,
        }
        return mapping.get(str(risk_level).lower(), 60.0)

    def _change_type_bonus(self, change_type: str) -> float:
        mapping = {
            "logging_patch": 18.0,
            "guard_patch": 20.0,
            "helper_patch": 18.0,
            "small_code_patch": 15.0,
            "config_patch": 8.0,
            "refactor": -10.0,
            "migration": -25.0,
            "cross_module_rewire": -30.0,
        }
        return mapping.get(str(change_type).lower(), 0.0)

    def _validation_scope_score(self, scope: str) -> float:
        mapping = {
            "local_compile_and_health": 90.0,
            "compile_and_health": 85.0,
            "compile_only": 65.0,
            "local_only": 50.0,
            "unknown": 25.0,
        }
        return mapping.get(str(scope).lower(), 40.0)

    def _rollback_ease_score(self, reversible: bool, touched_files_count: int) -> float:
        base = 85.0 if reversible else 35.0
        penalty = min(35.0, max(0, touched_files_count - 1) * 10.0)
        return max(0.0, base - penalty)

    def _blast_radius_score(self, touched_files_count: int, target_files: Sequence[str]) -> float:
        unique_targets = len(set(target_files))
        radius = min(100.0, (touched_files_count * 18.0) + (unique_targets * 10.0))
        return radius

    def _confidence_score(self, candidate: Dict[str, Any]) -> float:
        score = 45.0

        if candidate["risk_level"].lower() in {"very_low", "low"}:
            score += 18.0

        if candidate.get("expected_effect"):
            score += 8.0

        if candidate.get("reason"):
            score += 6.0

        if candidate.get("operations"):
            score += 12.0

        if candidate["estimated_validation_scope"].lower() in {"local_compile_and_health", "compile_and_health"}:
            score += 10.0

        metadata = candidate.get("metadata") or {}
        if metadata.get("verify") is True:
            score += 5.0

        return min(100.0, score)

    # ------------------------------------------------------------------
    # scoring
    # ------------------------------------------------------------------
    def score_candidate(self, candidate: Any) -> DecisionScorecard:
        c = self._normalize_candidate(candidate)
        touched_files_count = len(set(c.get("target_files") or []))
        operations_count = len(c.get("operations") or [])

        validation_coverage = self._validation_scope_score(c["estimated_validation_scope"])
        rollback_ease = self._rollback_ease_score(bool(c["reversible"]), touched_files_count)
        blast_radius = self._blast_radius_score(touched_files_count, c.get("target_files") or [])
        confidence = self._confidence_score(c)
        risk_score = self._risk_level_numeric(c["risk_level"])

        signals: List[RiskSignal] = []
        reasons: List[str] = []

        signals.append(RiskSignal("base_confidence", confidence, 0.30, "Structured candidate confidence"))
        signals.append(RiskSignal("validation_coverage", validation_coverage, 0.22, "Validation scope adequacy"))
        signals.append(RiskSignal("rollback_ease", rollback_ease, 0.18, "Ease of restoring previous state"))
        signals.append(RiskSignal("change_type_bonus", 50.0 + self._change_type_bonus(c["change_type"]), 0.10, "Patch category"))
        signals.append(RiskSignal("blast_radius_inverse", 100.0 - blast_radius, 0.10, "Smaller blast radius is safer"))
        signals.append(RiskSignal("operations_shape", max(0.0, 100.0 - max(0, operations_count - 2) * 12.0), 0.10, "Too many ops reduce safety"))

        weighted_total = 0.0
        total_weights = 0.0
        for s in signals:
            weighted_total += s.value * s.weight
            total_weights += s.weight

        normalized = weighted_total / total_weights if total_weights else 0.0

        # explicit gates
        gate_status = "open"
        recommended_action = "artifact_only"

        if c["risk_level"].lower() in {"critical"}:
            reasons.append("critical_risk_level")
            gate_status = "blocked"

        if touched_files_count == 0:
            reasons.append("no_target_files")
            gate_status = "blocked"

        if touched_files_count > 3:
            reasons.append("too_many_target_files")
            gate_status = "blocked"

        if blast_radius >= 75.0:
            reasons.append("blast_radius_too_large")

        if validation_coverage < 55.0:
            reasons.append("validation_coverage_too_low")

        if rollback_ease < 40.0:
            reasons.append("rollback_ease_too_low")

        if confidence < 55.0:
            reasons.append("confidence_too_low")

        if gate_status != "blocked":
            if normalized >= self.allow_apply_threshold and not reasons:
                recommended_action = "apply"
            elif normalized >= self.allow_apply_threshold and all(
                r in {"blast_radius_too_large"} for r in reasons
            ):
                recommended_action = "artifact_only"
            elif normalized >= self.artifact_only_threshold:
                recommended_action = "artifact_only"
            else:
                recommended_action = "blocked"
                gate_status = "blocked"
        else:
            recommended_action = "blocked"

        total_score = max(0.0, min(100.0, normalized - (risk_score * 0.08)))

        scorecard = DecisionScorecard(
            mutation_id=c["mutation_id"],
            total_score=round(total_score, 2),
            normalized_score=round(normalized, 2),
            confidence=round(confidence, 2),
            blast_radius=round(blast_radius, 2),
            rollback_ease=round(rollback_ease, 2),
            validation_coverage=round(validation_coverage, 2),
            risk_score=round(risk_score, 2),
            touched_files_count=touched_files_count,
            signals=signals,
            reasons=reasons,
            recommended_action=recommended_action,
        )

        self._write_json(self.scorecards_dir / f"{scorecard.mutation_id}.json", self._scorecard_payload(scorecard))
        return scorecard

    def decide(self, candidate: Any) -> DecisionOutcome:
        c = self._normalize_candidate(candidate)
        scorecard = self.score_candidate(c)

        if scorecard.recommended_action == "apply":
            allowed = True
            gate_status = "open"
            summary = "Low-risk mutation may be applied."
        elif scorecard.recommended_action == "artifact_only":
            allowed = False
            gate_status = "degraded"
            summary = "Candidate is useful for analysis/artifacts, but not safe enough for apply."
        else:
            allowed = False
            gate_status = "blocked"
            summary = "Candidate is blocked by risk or insufficient safety evidence."

        outcome = DecisionOutcome(
            mutation_id=scorecard.mutation_id,
            allowed=allowed,
            recommended_action=scorecard.recommended_action,
            gate_status=gate_status,
            summary=summary,
            reasons=scorecard.reasons,
            scorecard=scorecard,
        )

        self._write_json(self.outcomes_dir / f"{outcome.mutation_id}.json", self._outcome_payload(outcome))
        self._log(
            "info",
            f"decide mutation_id={outcome.mutation_id} action={outcome.recommended_action} "
            f"score={outcome.scorecard.total_score} gate={outcome.gate_status}",
        )
        return outcome

    def _scorecard_payload(self, scorecard: DecisionScorecard) -> Dict[str, Any]:
        data = asdict(scorecard)
        data["signals"] = [asdict(s) for s in scorecard.signals]
        return data

    def _outcome_payload(self, outcome: DecisionOutcome) -> Dict[str, Any]:
        data = asdict(outcome)
        data["scorecard"] = self._scorecard_payload(outcome.scorecard)
        return data

    def collect_metrics(self) -> Dict[str, Any]:
        scorecards = list(self.scorecards_dir.glob("*.json"))
        outcomes = list(self.outcomes_dir.glob("*.json"))

        apply_count = 0
        artifact_only_count = 0
        blocked_count = 0

        for path in outcomes:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                action = data.get("recommended_action")
                if action == "apply":
                    apply_count += 1
                elif action == "artifact_only":
                    artifact_only_count += 1
                elif action == "blocked":
                    blocked_count += 1
            except Exception:
                pass

        metrics = {
            "scorecards_count": len(scorecards),
            "outcomes_count": len(outcomes),
            "apply_count": apply_count,
            "artifact_only_count": artifact_only_count,
            "blocked_count": blocked_count,
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics