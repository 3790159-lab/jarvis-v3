from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ArtifactTaskType(str, Enum):
    TABLE = "table"
    SITE = "site"
    GAME = "game"
    YOUTUBE_PACK = "youtube_pack"


class ArtifactTaskRequest(BaseModel):
    task_type: ArtifactTaskType
    name: str = Field(..., min_length=3, max_length=120)
    description: str = Field(default="", max_length=5000)
    payload: dict[str, Any] = Field(default_factory=dict)
    overwrite: bool = False


class ArtifactFileRecord(BaseModel):
    relative_path: str
    size_bytes: int


class ArtifactManifest(BaseModel):
    task_id: str
    task_type: str
    name: str
    slug: str
    created_at: str
    output_dir: str
    files: list[ArtifactFileRecord] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    status: str = "created"

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
