from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MemoryWriteRequest(BaseModel):
    category: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryWriteResponse(BaseModel):
    ok: bool
    category: str
    title: str
    path: str
    error: Optional[str] = None


class MissionSummaryRequest(BaseModel):
    mission_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    lessons_learned: List[str] = Field(default_factory=list)
    known_issues: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MissionSummaryResponse(BaseModel):
    ok: bool
    mission_id: str
    files_written: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class MemoryListResponse(BaseModel):
    ok: bool
    category: str
    files: List[str] = Field(default_factory=list)
    error: Optional[str] = None
