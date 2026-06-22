import pytest

from app.services.block_m2_video.engines.router import EngineRouter


class _FakeEngine:
    engine_name = "fake_spicy"
    async def is_available(self): return True
    async def generate(self, request): ...


@pytest.mark.asyncio
async def test_router_spicy_returns_injected_wavespeed():
    router = EngineRouter(wavespeed=_FakeEngine())
    eng = await router.select("spicy")
    assert eng.engine_name == "fake_spicy"
