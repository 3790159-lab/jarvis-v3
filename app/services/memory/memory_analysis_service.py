from __future__ import annotations

from typing import List

from app.models.auto_memory import (
    AutoMemoryAnalysisResult,
    AutoMemoryMissionPayload,
)


class MemoryAnalysisService:
    def analyze(self, payload: AutoMemoryMissionPayload) -> AutoMemoryAnalysisResult:
        completed = []
        failed = []
        lessons: List[str] = []
        issues: List[str] = []

        for item in payload.task_results:
            status = (item.status or "").lower()

            if status in ("completed", "success", "ok"):
                completed.append(item)
            elif status in ("failed", "error"):
                failed.append(item)

            message_text = f"{item.message} {item.output}".lower()

            if status in ("completed", "success", "ok"):
                lesson = self._derive_lesson(item.title, item.message, item.output)
                if lesson:
                    lessons.append(lesson)

            if status in ("failed", "error"):
                issue = self._derive_issue(item.title, item.message, item.output)
                if issue:
                    issues.append(issue)

        if payload.summary and payload.summary.strip():
            summary_text = payload.summary.strip()
        else:
            summary_text = (
                f"Mission '{payload.mission_id}' finished with status '{payload.status}'. "
                f"Completed tasks: {len(completed)}. Failed tasks: {len(failed)}."
            )

        if not lessons and completed:
            lessons.append("Successful mission runs should automatically persist summaries and execution results.")
            lessons.append("Stable task decomposition improves completion quality across mission steps.")

        if not issues and failed:
            issues.append("One or more mission tasks failed and require follow-up analysis.")
        elif not failed:
            issues.append("No critical execution failures detected in this mission run.")

        tags = ["jarvis", "mission", "memory", payload.status.lower() or "unknown"]

        return AutoMemoryAnalysisResult(
            mission_id=payload.mission_id,
            objective=payload.objective,
            generated_summary=summary_text,
            lessons_learned=self._unique_nonempty(lessons)[:8],
            known_issues=self._unique_nonempty(issues)[:8],
            tags=self._unique_nonempty(tags),
        )

    def _derive_lesson(self, title: str, message: str, output: dict) -> str:
        text = f"{title} {message} {output}".strip()
        if not text:
            return ""
        if "file" in text.lower() and "written" in text.lower():
            return "File-writing tasks benefit from explicit output paths and result validation."
        if "route" in text.lower() or "router" in text.lower():
            return "Routing-related tasks work better with clear task typing and provider fallback."
        if "health" in text.lower():
            return "Health checks should be used before dependent execution steps."
        return f"Task '{title}' completed successfully and can be reused as a working execution pattern."

    def _derive_issue(self, title: str, message: str, output: dict) -> str:
        text = f"{title} {message} {output}".strip()
        if not text:
            return f"Task '{title}' failed."
        if "timeout" in text.lower():
            return f"Task '{title}' encountered a timeout and may need retry or provider adjustment."
        if "provider" in text.lower():
            return f"Task '{title}' had provider-related execution issues."
        if "connection" in text.lower():
            return f"Task '{title}' had a connectivity issue that should be validated before execution."
        return f"Task '{title}' failed with message: {message or 'unknown error'}"

    @staticmethod
    def _unique_nonempty(items: List[str]) -> List[str]:
        result: List[str] = []
        seen = set()
        for item in items:
            value = (item or "").strip()
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result
