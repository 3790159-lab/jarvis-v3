from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AutoMemoryTaskResult(BaseModel):
    task_id: Optional[str] = None
    title: str = ""
    status: str = ""
    message: str = ""
    output: Dict[str, Any] = Field(default_factory=dict)


class AutoMemoryMissionPayload(BaseModel):
    mission_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    status: str = "unknown"
    summary: str = ""
    task_results: List[AutoMemoryTaskResult] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AutoMemoryAnalysisResult(BaseModel):
    mission_id: str
    objective: str
    generated_summary: str
    lessons_learned: List[str] = Field(default_factory=list)
    known_issues: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class AutoMemoryRunResponse(BaseModel):
    ok: bool
    mission_id: str
    analysis: AutoMemoryAnalysisResult
    semantic_files: List[str] = Field(default_factory=list)
    obsidian_files: List[str] = Field(default_factory=list)
    error: Optional[str] = None
