from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MissionRunMemoryRequest(BaseModel):
    mission_result: Dict[str, Any] = Field(default_factory=dict)
    objective: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MissionRunMemoryResponse(BaseModel):
    ok: bool
    mission_id: str
    memory_written: bool
    semantic_files: List[str] = Field(default_factory=list)
    obsidian_files: List[str] = Field(default_factory=list)
    analysis: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
