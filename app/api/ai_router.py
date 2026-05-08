from __future__ import annotations

from fastapi import APIRouter

from app.models.ai_router import DispatchRequest, DispatchResult, RouterHealthResponse
from app.services.ai.ai_router_service import AIRouterService

router = APIRouter(prefix="/api/ai", tags=["ai-router"])

_service = AIRouterService()


@router.get("/health", response_model=RouterHealthResponse)
async def ai_router_health() -> RouterHealthResponse:
    return _service.health()


@router.post("/dispatch", response_model=DispatchResult)
async def ai_router_dispatch(request: DispatchRequest) -> DispatchResult:
    return await _service.dispatch(request)
