from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List

from app.models.memory_layer import (
    MemoryListResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
    MissionSummaryRequest,
    MissionSummaryResponse,
)


class SemanticMemoryService:
    def __init__(self) -> None:
        self.base_dir = Path("artifacts") / "memory"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write_note(self, request: MemoryWriteRequest) -> MemoryWriteResponse:
        try:
            category_dir = self.base_dir / self._safe_name(request.category)
            category_dir.mkdir(parents=True, exist_ok=True)

            filename = f"{self._timestamp()}_{self._safe_name(request.title)}.md"
            path = category_dir / filename

            tags_text = ""
            if request.tags:
                tags_text = " ".join(f"#{self._safe_name(tag)}" for tag in request.tags)

            metadata_lines = []
            for key, value in (request.metadata or {}).items():
                metadata_lines.append(f"- {key}: {value}")

            content = (
                f"# {request.title}\n\n"
                f"**Category:** {request.category}\n\n"
                f"**Created:** {datetime.utcnow().isoformat()}Z\n\n"
                f"{tags_text}\n\n"
                f"## Metadata\n"
                f"{chr(10).join(metadata_lines) if metadata_lines else '- none'}\n\n"
                f"## Content\n"
                f"{request.content}\n"
            )

            path.write_text(content, encoding="utf-8")

            return MemoryWriteResponse(
                ok=True,
                category=request.category,
                title=request.title,
                path=str(path),
                error=None,
            )
        except Exception as exc:
            return MemoryWriteResponse(
                ok=False,
                category=request.category,
                title=request.title,
                path="",
                error=str(exc),
            )

    def write_mission_summary(self, request: MissionSummaryRequest) -> MissionSummaryResponse:
        files_written: List[str] = []

        try:
            summary_result = self.write_note(
                MemoryWriteRequest(
                    category="summaries",
                    title=f"Mission Summary {request.mission_id}",
                    content=(
                        f"Mission ID: {request.mission_id}\n\n"
                        f"Objective:\n{request.objective}\n\n"
                        f"Summary:\n{request.summary}"
                    ),
                    tags=["mission", "summary"],
                    metadata=request.metadata,
                )
            )
            if summary_result.ok:
                files_written.append(summary_result.path)

            if request.lessons_learned:
                lessons_result = self.write_note(
                    MemoryWriteRequest(
                        category="lessons",
                        title=f"Lessons Learned {request.mission_id}",
                        content="\n".join(f"- {item}" for item in request.lessons_learned),
                        tags=["mission", "lessons"],
                        metadata={"mission_id": request.mission_id, **request.metadata},
                    )
                )
                if lessons_result.ok:
                    files_written.append(lessons_result.path)

            if request.known_issues:
                issues_result = self.write_note(
                    MemoryWriteRequest(
                        category="issues",
                        title=f"Known Issues {request.mission_id}",
                        content="\n".join(f"- {item}" for item in request.known_issues),
                        tags=["mission", "issues"],
                        metadata={"mission_id": request.mission_id, **request.metadata},
                    )
                )
                if issues_result.ok:
                    files_written.append(issues_result.path)

            mission_result = self.write_note(
                MemoryWriteRequest(
                    category="missions",
                    title=f"Mission Record {request.mission_id}",
                    content=(
                        f"Mission ID: {request.mission_id}\n\n"
                        f"Objective:\n{request.objective}\n\n"
                        f"Summary:\n{request.summary}\n\n"
                        f"Lessons learned:\n"
                        f"{chr(10).join(f'- {item}' for item in request.lessons_learned) if request.lessons_learned else '- none'}\n\n"
                        f"Known issues:\n"
                        f"{chr(10).join(f'- {item}' for item in request.known_issues) if request.known_issues else '- none'}\n"
                    ),
                    tags=["mission", "record"],
                    metadata=request.metadata,
                )
            )
            if mission_result.ok:
                files_written.append(mission_result.path)

            return MissionSummaryResponse(
                ok=True,
                mission_id=request.mission_id,
                files_written=files_written,
                error=None,
            )
        except Exception as exc:
            return MissionSummaryResponse(
                ok=False,
                mission_id=request.mission_id,
                files_written=files_written,
                error=str(exc),
            )

    def list_category(self, category: str) -> MemoryListResponse:
        try:
            category_dir = self.base_dir / self._safe_name(category)
            if not category_dir.exists():
                return MemoryListResponse(ok=True, category=category, files=[])

            files = sorted([str(p) for p in category_dir.glob("*.md")], reverse=True)
            return MemoryListResponse(ok=True, category=category, files=files)
        except Exception as exc:
            return MemoryListResponse(ok=False, category=category, files=[], error=str(exc))

    @staticmethod
    def _safe_name(value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value)
        return value.strip("_") or "item"

    @staticmethod
    def _timestamp() -> str:
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")
