from __future__ import annotations

from typing import List, Tuple

from app.models.ai_router import TaskType
from app.models.mission_ai import MissionAITask, TaskClassificationResult


class TaskClassifier:
    """
    Lightweight heuristic classifier for mission tasks.
    Safe first version for Block 2.
    """

    def classify(self, task: MissionAITask, objective: str = "") -> TaskClassificationResult:
        text = f"{objective}\n{task.title}\n{task.description}".lower()

        coding_keywords = [
            "code", "python", "fastapi", "api", "bug", "fix", "refactor", "script",
            "function", "class", "endpoint", "backend", "frontend", "test", "uvicorn",
            "powershell", "module", "router", "service", "database", "sql"
        ]
        research_keywords = [
            "research", "find", "search", "analyze market", "look up", "collect data",
            "compare options", "sources", "documentation", "docs", "investigate", "web"
        ]
        reasoning_keywords = [
            "plan", "strategy", "roadmap", "compare", "choose", "decide", "architecture",
            "tradeoff", "prioritize", "evaluate", "assess", "design"
        ]

        scored: List[Tuple[TaskType, int, List[str]]] = []

        scored.append((TaskType.CODING, self._count_matches(text, coding_keywords), self._matched_terms(text, coding_keywords)))
        scored.append((TaskType.RESEARCH, self._count_matches(text, research_keywords), self._matched_terms(text, research_keywords)))
        scored.append((TaskType.REASONING, self._count_matches(text, reasoning_keywords), self._matched_terms(text, reasoning_keywords)))

        scored.sort(key=lambda item: item[1], reverse=True)

        best_type, best_score, reasons = scored[0]

        if best_score <= 0:
            return TaskClassificationResult(
                task_id=task.task_id,
                title=task.title,
                detected_task_type=TaskType.GENERAL,
                confidence=0.35,
                reasons=["No strong specialized keyword match; defaulted to general"],
            )

        confidence = min(0.95, 0.45 + (best_score * 0.12))

        return TaskClassificationResult(
            task_id=task.task_id,
            title=task.title,
            detected_task_type=best_type,
            confidence=confidence,
            reasons=reasons[:6] if reasons else [f"Matched {best_type.value} pattern"],
        )

    @staticmethod
    def _count_matches(text: str, keywords: List[str]) -> int:
        count = 0
        for keyword in keywords:
            if keyword in text:
                count += 1
        return count

    @staticmethod
    def _matched_terms(text: str, keywords: List[str]) -> List[str]:
        matched = []
        for keyword in keywords:
            if keyword in text:
                matched.append(keyword)
        return matched
