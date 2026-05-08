from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.jarvis_internet_tools import (
    internet_health,
    internet_search,
    internet_research,
    find_pipelines,
    compare_services,
    engineer_brief,
    run_internet_tool,
)


router = APIRouter(prefix="/api/jarvis/tools/internet", tags=["jarvis-internet-tools"])


class SearchRequest(BaseModel):
    query: str
    max_results: int = Field(default=5)
    search_depth: str = Field(default="basic")


class ResearchRequest(BaseModel):
    query: str
    system_prompt: Optional[str] = None


class PipelineRequest(BaseModel):
    topic: str
    max_results: int = Field(default=6)


class CompareRequest(BaseModel):
    goal: str
    services: List[str] = Field(default_factory=list)


class EngineerRequest(BaseModel):
    task: str


class ToolRequest(BaseModel):
    tool_name: str
    args: Dict[str, Any] = Field(default_factory=dict)


@router.get("/health")
def health() -> Dict[str, Any]:
    return internet_health()


@router.post("/search")
def search(req: SearchRequest) -> Dict[str, Any]:
    return internet_search(req.query, req.max_results, req.search_depth)


@router.post("/research")
def research(req: ResearchRequest) -> Dict[str, Any]:
    return internet_research(req.query, req.system_prompt)


@router.post("/find-pipelines")
def pipelines(req: PipelineRequest) -> Dict[str, Any]:
    return find_pipelines(req.topic, req.max_results)


@router.post("/compare-services")
def compare(req: CompareRequest) -> Dict[str, Any]:
    return compare_services(req.goal, req.services)


@router.post("/engineer-brief")
def brief(req: EngineerRequest) -> Dict[str, Any]:
    return engineer_brief(req.task)


@router.post("/run-tool")
def run_tool(req: ToolRequest) -> Dict[str, Any]:
    return run_internet_tool(req.tool_name, req.args)