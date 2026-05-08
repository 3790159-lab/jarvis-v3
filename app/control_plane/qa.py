from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field

from .models import AgentResult, TaskSpec
from .strategy_runtime import StrategyRuntimeStore


class QAEvaluation(BaseModel):
    passed: bool
    scores: dict[str, float] = Field(default_factory=dict)
    summary: str = ""
    repair_hints: list[str] = Field(default_factory=list)


class QAGate:
    def __init__(self, strategy_path: str | Path = "state/agent_mesh/strategy_runtime.json") -> None:
        self.strategy = StrategyRuntimeStore(Path(strategy_path))

    def _handoff_map(self, result: AgentResult) -> dict[str, str]:
        out = {}
        for item in list(result.handoff_notes or []):
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def evaluate(self, task: TaskSpec, result: AgentResult) -> QAEvaluation:
        meta = self._handoff_map(result)
        mode = str(meta.get("mode") or "").lower()
        service = str(meta.get("service") or "").lower()
        dry_run = False

        normalized_text = str(result.normalized_text or "")
        if '"dry_run": true' in normalized_text.lower():
            dry_run = True
        if "dry_run=True" in " ".join(result.handoff_notes or []):
            dry_run = True

        completeness = 1.0 if (result.summary or result.normalized_text or result.artifacts) else 0.0
        correctness = 0.9 if result.status == "ok" and not result.error else 0.2
        stability = 0.9 if not result.error else 0.3
        recoverability = 0.8 if result.next_actions or result.validation_hints or not result.error else 0.4
        usefulness = 0.9 if result.summary else 0.4

        if mode == "fallback_template":
            correctness = min(correctness, 0.62)
            usefulness = min(usefulness, 0.58)
            stability = min(stability, 0.75)

        if task.task_type == "integration":
            if dry_run:
                correctness = min(correctness, 0.78)
                usefulness = min(usefulness, 0.68)
            if mode == "guarded_binding_check" or "config_check" in normalized_text.lower():
                usefulness = min(usefulness, 0.72)
            if service == "":
                correctness = min(correctness, 0.7)
                usefulness = min(usefulness, 0.65)
            if result.artifacts:
                completeness = max(completeness, 0.95)

        if task.task_type == "codegen":
            if not result.artifacts and len(normalized_text.strip()) < 180:
                completeness = min(completeness, 0.45)
                usefulness = min(usefulness, 0.55)
            if result.artifacts:
                completeness = max(completeness, 0.95)

        generic_markers = [
            "Relevant context gathered.",
            "Validation passed.",
            "Memory bundle persisted.",
            "Mission graph prepared",
        ]
        if any(marker in normalized_text for marker in generic_markers) and mode == "fallback_template":
            usefulness = min(usefulness, 0.55)
            correctness = min(correctness, 0.65)

        scores = {
            "correctness": round(correctness, 2),
            "completeness": round(completeness, 2),
            "stability": round(stability, 2),
            "recoverability": round(recoverability, 2),
            "usefulness": round(usefulness, 2),
        }
        avg = sum(scores.values()) / len(scores)

        strictness = str(self.strategy.get_knob("qa_strictness", "medium") or "medium").lower()
        min_avg = 0.68
        min_correctness = 0.6

        if strictness == "high":
            min_avg = 0.75
            min_correctness = 0.7
        elif strictness == "low":
            min_avg = 0.6
            min_correctness = 0.55

        passed = avg >= min_avg and correctness >= min_correctness

        hints: list[str] = []
        if not passed:
            if completeness < 0.6:
                hints.append("Return fuller normalized_text or attach useful artifacts.")
            if correctness < min_correctness:
                hints.append("Use stronger provider/service preference or a better grounded execution path.")
            if usefulness < 0.7:
                hints.append("Avoid generic outputs; return task-specific value.")
            if mode == "fallback_template":
                hints.append("Fallback template was used. Promote to live adapter path or tighten routing.")

        return QAEvaluation(
            passed=passed,
            scores=scores,
            summary=f"QA average={avg:.2f} passed={passed} strictness={strictness} mode={mode or 'default'}",
            repair_hints=hints,
        )