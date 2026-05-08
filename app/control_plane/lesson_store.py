from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field


class LessonRecord(BaseModel):
    lesson_id: str
    agent_id: str
    capability: str
    task_type: str
    title: str
    summary: str
    preferred_service: str = ""
    recommended_actions: list[str] = Field(default_factory=list)
    caution_flags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source_mission_id: str = ""
    source_task_id: str = ""
    created_ts: float = 0.0


class LessonStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lessons: list[LessonRecord] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"lessons": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.lessons = [LessonRecord.model_validate(x) for x in raw.get("lessons", [])]

    def save(self) -> None:
        data = {"lessons": [x.model_dump(mode="json") for x in self.lessons[-600:]]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_lesson(
        self,
        agent_id: str,
        capability: str,
        task_type: str,
        title: str,
        summary: str,
        preferred_service: str,
        recommended_actions: list[str],
        caution_flags: list[str],
        tags: list[str],
        confidence: float,
        source_mission_id: str,
        source_task_id: str,
    ) -> LessonRecord:
        lesson = LessonRecord(
            lesson_id=f"lesson_{uuid.uuid4().hex[:10]}",
            agent_id=agent_id,
            capability=capability,
            task_type=task_type,
            title=title,
            summary=summary,
            preferred_service=preferred_service or "",
            recommended_actions=list(recommended_actions or []),
            caution_flags=list(caution_flags or []),
            tags=list(tags or []),
            confidence=float(confidence or 0.0),
            source_mission_id=source_mission_id,
            source_task_id=source_task_id,
            created_ts=time.time(),
        )
        self.lessons.append(lesson)
        self.save()
        return lesson

    def list_lessons(self, limit: int = 100) -> list[LessonRecord]:
        ordered = sorted(self.lessons, key=lambda x: (-x.confidence, -x.created_ts))
        return ordered[:max(1, limit)]

    def summary(self) -> dict:
        return {
            "lessons_count": len(self.lessons),
        }