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
class PatchBudget:
    risk_lane: str
    max_files: int
    max_operations: int
    max_lines_changed: int
    requires_verify: bool
    requires_smoke: bool
    requires_health: bool
    rollback_required: bool
    allowed_action: str


@dataclass
class PatchClassification:
    classification_id: str
    goal: str
    task: str
    patch_class: str
    risk_lane: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class BlastRadiusReport:
    target_files: List[str]
    support_files: List[str]
    total_files: int
    critical_files: List[str] = field(default_factory=list)
    forbidden_files: List[str] = field(default_factory=list)
    touched_directories: List[str] = field(default_factory=list)
    estimated_lines_changed: int = 0
    blast_radius_score: float = 0.0
    reasons: List[str] = field(default_factory=list)


@dataclass
class AdvancedMutationDecision:
    decision_id: str
    patch_class: str
    risk_lane: str
    recommended_action: str
    allowed: bool
    confidence: float
    budget: PatchBudget
    blast_radius: BlastRadiusReport
    reasons: List[str] = field(default_factory=list)
    next_best_action: str = ""
    created_at: str = field(default_factory=utc_now_iso)


class JarvisAdvancedMutationLane:
    """
    Block 7.1 + 7.2:
    - classify desired mutation type
    - assign risk lane
    - create patch budget
    - estimate blast radius
    - decide apply / artifact_only / blocked

    This module does NOT apply patches yet.
    It prepares safe constraints for the next dependency-aware patch planner.
    """

    FORBIDDEN_PATTERNS = [
        r"(^|/|\\)\.git($|/|\\)",
        r"(^|/|\\)\.venv($|/|\\)",
        r"(^|/|\\)venv($|/|\\)",
        r"(^|/|\\)\.env$",
        r"(^|/|\\)secrets?($|/|\\)",
        r"(^|/|\\)__pycache__($|/|\\)",
    ]

    CRITICAL_FILE_HINTS = [
        "app/main.py",
        "app\\main.py",
        "settings.py",
        "config.py",
        "router",
        "database",
        "migration",
    ]

    PATCH_CLASS_KEYWORDS = {
        "logging_patch": [
            "log",
            "logging",
            "observability",
            "trace",
            "diagnosis",
            "diagnostic",
            "debug",
        ],
        "guard_patch": [
            "guard",
            "none",
            "null",
            "fallback",
            "crash",
            "exception",
            "runtime",
            "strictmode",
            "safe",
        ],
        "validation_patch": [
            "validation",
            "validate",
            "verify",
            "smoke",
            "compile",
            "test",
            "health-check",
            "health check",
        ],
        "health_patch": [
            "health",
            "endpoint",
            "status",
            "readiness",
            "probe",
        ],
        "config_safe_patch": [
            "config",
            "env",
            "setting",
            "configuration",
            "toggle",
        ],
        "small_refactor": [
            "refactor",
            "cleanup",
            "simplify",
            "deduplicate",
            "helper",
            "extract",
        ],
        "two_file_coordination_patch": [
            "coordination",
            "handoff",
            "planner",
            "coder",
            "validator",
            "risk guardian",
            "agent",
        ],
        "external_readiness_patch": [
            "telegram",
            "n8n",
            "google",
            "workspace",
            "external",
            "readiness",
            "integration",
        ],
    }

    def __init__(
        self,
        project_root: str | Path,
        artifacts_root: Optional[str | Path] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "advanced_mutation_lane"
        )

        self.classifications_dir = ensure_dir(self.artifacts_root / "classifications")
        self.decisions_dir = ensure_dir(self.artifacts_root / "decisions")
        self.blast_radius_dir = ensure_dir(self.artifacts_root / "blast_radius")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "advanced_mutation_lane.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def classify(self, goal: str, task: str) -> PatchClassification:
        text = f"{goal} {task}".lower()
        scores: Dict[str, int] = {}

        for patch_class, keywords in self.PATCH_CLASS_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword in text:
                    score += 1
            scores[patch_class] = score

        best_class = max(scores, key=lambda k: scores[k])
        best_score = scores[best_class]

        reasons: List[str] = []
        if best_score == 0:
            best_class = "guard_patch"
            reasons.append("no_clear_class_detected_defaulting_to_guard_patch")
            confidence = 45.0
        else:
            reasons.append(f"matched_keywords_for:{best_class}")
            confidence = min(95.0, 55.0 + best_score * 10.0)

        risk_lane = self._default_risk_lane(best_class, text)

        classification = PatchClassification(
            classification_id="cls_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            goal=goal,
            task=task,
            patch_class=best_class,
            risk_lane=risk_lane,
            confidence=round(confidence, 2),
            reasons=reasons,
        )
        self._write_json(self.classifications_dir / f"{classification.classification_id}.json", asdict(classification))
        self._log("info", f"classify patch_class={classification.patch_class} risk_lane={classification.risk_lane} confidence={classification.confidence}")
        return classification

    def _default_risk_lane(self, patch_class: str, text: str) -> str:
        if any(k in text for k in ["migration", "rewrite", "major", "delete", "remove critical"]):
            return "medium"
        if patch_class in {"logging_patch", "guard_patch", "validation_patch", "health_patch"}:
            return "low"
        if patch_class in {"small_refactor", "two_file_coordination_patch", "external_readiness_patch"}:
            return "medium_low"
        if patch_class == "config_safe_patch":
            return "medium_low"
        return "low"

    def budget_for(self, patch_class: str, risk_lane: str) -> PatchBudget:
        risk_lane = risk_lane.lower()

        if risk_lane == "low":
            return PatchBudget(
                risk_lane=risk_lane,
                max_files=1,
                max_operations=2,
                max_lines_changed=70,
                requires_verify=True,
                requires_smoke=False,
                requires_health=True,
                rollback_required=True,
                allowed_action="apply",
            )

        if risk_lane == "medium_low":
            return PatchBudget(
                risk_lane=risk_lane,
                max_files=3,
                max_operations=5,
                max_lines_changed=160,
                requires_verify=True,
                requires_smoke=True,
                requires_health=True,
                rollback_required=True,
                allowed_action="apply",
            )

        if risk_lane == "medium":
            return PatchBudget(
                risk_lane=risk_lane,
                max_files=3,
                max_operations=6,
                max_lines_changed=220,
                requires_verify=True,
                requires_smoke=True,
                requires_health=True,
                rollback_required=True,
                allowed_action="artifact_only",
            )

        return PatchBudget(
            risk_lane=risk_lane,
            max_files=0,
            max_operations=0,
            max_lines_changed=0,
            requires_verify=True,
            requires_smoke=True,
            requires_health=True,
            rollback_required=True,
            allowed_action="blocked",
        )

    def estimate_blast_radius(
        self,
        target_files: Sequence[str],
        support_files: Optional[Sequence[str]] = None,
        estimated_lines_changed: int = 50,
    ) -> BlastRadiusReport:
        support_files = list(support_files or [])
        target_files = list(target_files or [])
        all_files = target_files + support_files

        critical_files: List[str] = []
        forbidden_files: List[str] = []
        touched_dirs: List[str] = []
        reasons: List[str] = []

        for rel in all_files:
            normalized = rel.replace("\\", "/").strip()
            touched_dirs.append(str(Path(normalized).parent).replace("\\", "/"))

            for pat in self.FORBIDDEN_PATTERNS:
                if re.search(pat, normalized, flags=re.IGNORECASE):
                    forbidden_files.append(rel)
                    reasons.append(f"forbidden_path:{rel}")

            lowered = normalized.lower()
            for hint in self.CRITICAL_FILE_HINTS:
                if hint.lower().replace("\\", "/") in lowered:
                    critical_files.append(rel)
                    reasons.append(f"critical_file_hint:{rel}")
                    break

        total_files = len(set(all_files))
        dir_count = len(set(touched_dirs))

        score = 0.0
        target_count = len(set(target_files))
        support_count = len(set(support_files))
        score += target_count * 18.0
        score += support_count * 8.0
        score += dir_count * 4.0
        score += len(critical_files) * 22.0
        score += len(forbidden_files) * 100.0
        score += max(0, estimated_lines_changed - 80) * 0.25

        if total_files == 0:
            reasons.append("no_files_declared")
            score += 50.0

        report = BlastRadiusReport(
            target_files=target_files,
            support_files=support_files,
            total_files=total_files,
            critical_files=sorted(set(critical_files)),
            forbidden_files=sorted(set(forbidden_files)),
            touched_directories=sorted(set(touched_dirs)),
            estimated_lines_changed=estimated_lines_changed,
            blast_radius_score=round(min(100.0, score), 2),
            reasons=sorted(set(reasons)),
        )
        self._write_json(
            self.blast_radius_dir / ("blast_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8] + ".json"),
            asdict(report),
        )
        return report

    def decide(
        self,
        goal: str,
        task: str,
        target_files: Sequence[str],
        support_files: Optional[Sequence[str]] = None,
        estimated_lines_changed: int = 50,
    ) -> AdvancedMutationDecision:
        classification = self.classify(goal, task)

        support_files = list(support_files or [])
        target_files = list(target_files or [])
        total_declared_files = len(set(target_files + support_files))

        effective_risk_lane = classification.risk_lane
        if (
            effective_risk_lane == "low"
            and not any(str(p).lower().endswith(".env") for p in target_files + support_files)
            and (support_files or total_declared_files > 1 or estimated_lines_changed > 70)
        ):
            effective_risk_lane = "medium_low"

        budget = self.budget_for(classification.patch_class, effective_risk_lane)
        blast = self.estimate_blast_radius(
            target_files=target_files,
            support_files=support_files,
            estimated_lines_changed=estimated_lines_changed,
        )

        reasons: List[str] = []
        action = budget.allowed_action
        allowed = action == "apply"

        if classification.confidence < 50:
            reasons.append("classification_confidence_too_low")
            action = "artifact_only"
            allowed = False

        if blast.forbidden_files:
            reasons.append("forbidden_files_detected")
            action = "blocked"
            allowed = False

        if blast.total_files > budget.max_files:
            reasons.append(f"file_budget_exceeded:{blast.total_files}>{budget.max_files}")
            action = "artifact_only" if action != "blocked" else "blocked"
            allowed = False

        if estimated_lines_changed > budget.max_lines_changed:
            reasons.append(f"line_budget_exceeded:{estimated_lines_changed}>{budget.max_lines_changed}")
            action = "artifact_only" if action != "blocked" else "blocked"
            allowed = False

        if blast.blast_radius_score >= 80:
            reasons.append("blast_radius_too_high")
            action = "blocked"
            allowed = False
        elif blast.blast_radius_score >= 55 and action == "apply":
            reasons.append("blast_radius_requires_artifact_review")
            action = "artifact_only"
            allowed = False

        if effective_risk_lane == "medium":
            reasons.append("medium_risk_requires_manual_or_next_stage_review")
            if action != "blocked":
                action = "artifact_only"
            allowed = False

        # Hard safety override: forbidden targets must always block apply/review flow.
        if blast.forbidden_files:
            action = "blocked"
            allowed = False
            if "forbidden_files_detected" not in reasons:
                reasons.append("forbidden_files_detected")

        if not reasons and action == "apply":
            next_best_action = "Generate dependency-aware patch plan and route through safe mutation foundation."
        elif action == "artifact_only":
            next_best_action = "Reduce scope or add support verify/smoke files before apply."
        else:
            next_best_action = "Do not apply. Remove forbidden targets or reduce blast radius."

        decision = AdvancedMutationDecision(
            decision_id="advdec_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8],
            patch_class=classification.patch_class,
            risk_lane=effective_risk_lane,
            recommended_action=action,
            allowed=allowed,
            confidence=classification.confidence,
            budget=budget,
            blast_radius=blast,
            reasons=reasons + classification.reasons + blast.reasons,
            next_best_action=next_best_action,
        )

        self._write_json(self.decisions_dir / f"{decision.decision_id}.json", self._decision_payload(decision))
        self._log("info", f"decide action={decision.recommended_action} patch_class={decision.patch_class} risk={decision.risk_lane} blast={blast.blast_radius_score}")
        return decision

    def _decision_payload(self, decision: AdvancedMutationDecision) -> Dict[str, Any]:
        return {
            "decision_id": decision.decision_id,
            "patch_class": decision.patch_class,
            "risk_lane": decision.risk_lane,
            "recommended_action": decision.recommended_action,
            "allowed": decision.allowed,
            "confidence": decision.confidence,
            "budget": asdict(decision.budget),
            "blast_radius": asdict(decision.blast_radius),
            "reasons": decision.reasons,
            "next_best_action": decision.next_best_action,
            "created_at": decision.created_at,
        }

    def collect_metrics(self) -> Dict[str, Any]:
        decisions = []
        for path in self.decisions_dir.glob("*.json"):
            try:
                decisions.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass

        apply_count = sum(1 for d in decisions if d.get("recommended_action") == "apply")
        artifact_only_count = sum(1 for d in decisions if d.get("recommended_action") == "artifact_only")
        blocked_count = sum(1 for d in decisions if d.get("recommended_action") == "blocked")

        class_counts: Dict[str, int] = {}
        for d in decisions:
            pc = d.get("patch_class", "unknown")
            class_counts[pc] = class_counts.get(pc, 0) + 1

        metrics = {
            "decisions_count": len(decisions),
            "apply_count": apply_count,
            "artifact_only_count": artifact_only_count,
            "blocked_count": blocked_count,
            "patch_class_counts": class_counts,
            "collected_at": utc_now_iso(),
            "artifacts_root": str(self.artifacts_root),
        }
        self._write_json(self.runtime_dir / "metrics_summary.json", metrics)
        return metrics