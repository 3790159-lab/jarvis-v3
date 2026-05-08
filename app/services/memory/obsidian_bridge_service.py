from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List

from app.models.obsidian_bridge import (
    AutoMissionMemoryRequest,
    AutoMissionMemoryResponse,
    ObsidianExportRequest,
    ObsidianExportResponse,
)
from app.models.memory_layer import MissionSummaryRequest
from app.services.memory.semantic_memory_service import SemanticMemoryService


class ObsidianBridgeService:
    def __init__(self) -> None:
        self.semantic_memory = SemanticMemoryService()
        self.vault_path = Path(
            os.getenv(
                "JARVIS_OBSIDIAN_VAULT_PATH",
                str(Path("artifacts") / "obsidian_vault")
            )
        )

    def ensure_vault_structure(self) -> List[str]:
        folders = [
            "00_Inbox",
            "01_Missions",
            "02_Lessons",
            "03_Issues",
            "04_Architecture",
            "05_Playbooks",
            "99_System",
        ]
        created = []
        self.vault_path.mkdir(parents=True, exist_ok=True)
        for folder in folders:
            folder_path = self.vault_path / folder
            folder_path.mkdir(parents=True, exist_ok=True)
            created.append(str(folder_path))
        return created

    def export_note(self, request: ObsidianExportRequest) -> ObsidianExportResponse:
        try:
            self.ensure_vault_structure()

            folder_name = self._map_category_to_folder(request.category)
            target_dir = self.vault_path / folder_name
            target_dir.mkdir(parents=True, exist_ok=True)

            file_name = f"{self._timestamp()}_{self._safe_name(request.title)}.md"
            note_path = target_dir / file_name

            frontmatter = self._build_frontmatter(
                tags=request.tags,
                metadata=request.metadata,
                category=request.category,
            )
            links_block = ""
            if request.links:
                links_block = "## Links\n" + "\n".join(f"- [[{link}]]" for link in request.links) + "\n\n"

            content = (
                f"{frontmatter}"
                f"# {request.title}\n\n"
                f"{links_block}"
                f"## Content\n"
                f"{request.content}\n"
            )

            note_path.write_text(content, encoding="utf-8")

            return ObsidianExportResponse(
                ok=True,
                vault_path=str(self.vault_path),
                note_path=str(note_path),
                error=None,
            )
        except Exception as exc:
            return ObsidianExportResponse(
                ok=False,
                vault_path=str(self.vault_path),
                note_path="",
                error=str(exc),
            )

    def auto_write_mission_memory(self, request: AutoMissionMemoryRequest) -> AutoMissionMemoryResponse:
        semantic_files: List[str] = []
        obsidian_files: List[str] = []

        try:
            semantic_result = self.semantic_memory.write_mission_summary(
                MissionSummaryRequest(
                    mission_id=request.mission_id,
                    objective=request.objective,
                    summary=request.summary,
                    lessons_learned=request.lessons_learned,
                    known_issues=request.known_issues,
                    metadata=request.metadata,
                )
            )
            if semantic_result.ok:
                semantic_files.extend(semantic_result.files_written)

            mission_note = self.export_note(
                ObsidianExportRequest(
                    category="missions",
                    title=f"Mission {request.mission_id}",
                    content=(
                        f"**Objective:** {request.objective}\n\n"
                        f"## Summary\n{request.summary}\n\n"
                        f"## Lessons Learned\n"
                        f"{chr(10).join(f'- {x}' for x in request.lessons_learned) if request.lessons_learned else '- none'}\n\n"
                        f"## Known Issues\n"
                        f"{chr(10).join(f'- {x}' for x in request.known_issues) if request.known_issues else '- none'}\n"
                    ),
                    tags=["jarvis", "mission", *request.tags],
                    metadata={"mission_id": request.mission_id, **request.metadata},
                    links=[
                        f"Lessons {request.mission_id}",
                        f"Issues {request.mission_id}",
                    ],
                )
            )
            if mission_note.ok:
                obsidian_files.append(mission_note.note_path)

            if request.lessons_learned:
                lessons_note = self.export_note(
                    ObsidianExportRequest(
                        category="lessons",
                        title=f"Lessons {request.mission_id}",
                        content="\n".join(f"- {item}" for item in request.lessons_learned),
                        tags=["jarvis", "lessons", *request.tags],
                        metadata={"mission_id": request.mission_id, **request.metadata},
                        links=[f"Mission {request.mission_id}"],
                    )
                )
                if lessons_note.ok:
                    obsidian_files.append(lessons_note.note_path)

            if request.known_issues:
                issues_note = self.export_note(
                    ObsidianExportRequest(
                        category="issues",
                        title=f"Issues {request.mission_id}",
                        content="\n".join(f"- {item}" for item in request.known_issues),
                        tags=["jarvis", "issues", *request.tags],
                        metadata={"mission_id": request.mission_id, **request.metadata},
                        links=[f"Mission {request.mission_id}"],
                    )
                )
                if issues_note.ok:
                    obsidian_files.append(issues_note.note_path)

            return AutoMissionMemoryResponse(
                ok=True,
                semantic_files=semantic_files,
                obsidian_files=obsidian_files,
                error=None,
            )
        except Exception as exc:
            return AutoMissionMemoryResponse(
                ok=False,
                semantic_files=semantic_files,
                obsidian_files=obsidian_files,
                error=str(exc),
            )

    @staticmethod
    def _map_category_to_folder(category: str) -> str:
        value = category.strip().lower()
        mapping = {
            "missions": "01_Missions",
            "lessons": "02_Lessons",
            "issues": "03_Issues",
            "architecture": "04_Architecture",
            "playbooks": "05_Playbooks",
        }
        return mapping.get(value, "00_Inbox")

    @staticmethod
    def _safe_name(value: str) -> str:
        value = value.strip()
        value = re.sub(r"[^a-zA-Z0-9_\- ]+", "_", value)
        value = re.sub(r"\s+", "_", value)
        return value.strip("_") or "note"

    @staticmethod
    def _timestamp() -> str:
        return datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _build_frontmatter(tags: list[str], metadata: dict, category: str) -> str:
        lines = ["---"]
        lines.append(f'category: "{category}"')
        lines.append(f'created_utc: "{datetime.utcnow().isoformat()}Z"')
        clean_tags = tags or []
        if clean_tags:
            tags_str = ", ".join(f'"{tag}"' for tag in clean_tags)
            lines.append(f"tags: [{tags_str}]")
        else:
            lines.append("tags: []")

        for key, value in (metadata or {}).items():
            safe_key = re.sub(r"[^a-zA-Z0-9_]+", "_", str(key)).strip("_") or "meta"
            safe_value = str(value).replace('"', "'")
            lines.append(f'{safe_key}: "{safe_value}"')

        lines.append("---")
        lines.append("")
        return "\n".join(lines)
