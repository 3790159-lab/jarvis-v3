from __future__ import annotations

from fastapi import APIRouter

from app.models.provider_routing_policy import (
    ProviderRoutingDecision,
    ProviderRoutingHealthResponse,
    ProviderRoutingRequest,
)
from app.services.ai.provider_routing_policy_service import ProviderRoutingPolicyService

router = APIRouter(prefix="/api/provider-routing", tags=["provider-routing"])

_service = ProviderRoutingPolicyService()


@router.get("/health", response_model=ProviderRoutingHealthResponse)
async def provider_routing_health() -> ProviderRoutingHealthResponse:
    return _service.health()


@router.post("/decide", response_model=ProviderRoutingDecision)
async def provider_routing_decide(request: ProviderRoutingRequest) -> ProviderRoutingDecision:
    return _service.decide(request)
