from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ObsidianExportRequest(BaseModel):
    category: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    links: List[str] = Field(default_factory=list)


class ObsidianExportResponse(BaseModel):
    ok: bool
    vault_path: str
    note_path: str
    error: Optional[str] = None


class AutoMissionMemoryRequest(BaseModel):
    mission_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    lessons_learned: List[str] = Field(default_factory=list)
    known_issues: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AutoMissionMemoryResponse(BaseModel):
    ok: bool
    semantic_files: List[str] = Field(default_factory=list)
    obsidian_files: List[str] = Field(default_factory=list)
    error: Optional[str] = None
