# -*- coding: utf-8 -*-
"""Tests for ``EngineRouter`` mode selection."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.block_m2_video.engines.router import EngineRouter


def _make_engine(name: str, available: bool):
    e = MagicMock()
    e.engine_name = name
    e.is_available = AsyncMock(return_value=available)
    return e


@pytest.mark.anyio
async def test_mode_fast_returns_replicate():
    replicate = _make_engine("replicate", available=True)
    runpod = _make_engine("runpod_comfy", available=True)
    router = EngineRouter(replicate=replicate, runpod=runpod)

    chosen = await router.select("fast")

    assert chosen is replicate
    runpod.is_available.assert_not_awaited()


@pytest.mark.anyio
async def test_mode_hq_returns_runpod():
    replicate = _make_engine("replicate", available=True)
    runpod = _make_engine("runpod_comfy", available=False)
    router = EngineRouter(replicate=replicate, runpod=runpod)

    chosen = await router.select("hq")

    assert chosen is runpod
    # hq never asks availability — it's a forced choice
    runpod.is_available.assert_not_awaited()


@pytest.mark.anyio
async def test_mode_auto_picks_runpod_when_available():
    replicate = _make_engine("replicate", available=True)
    runpod = _make_engine("runpod_comfy", available=True)
    router = EngineRouter(replicate=replicate, runpod=runpod)

    chosen = await router.select("auto")

    assert chosen is runpod
    runpod.is_available.assert_awaited_once()


@pytest.mark.anyio
async def test_mode_auto_falls_back_to_replicate_when_runpod_unavailable():
    replicate = _make_engine("replicate", available=True)
    runpod = _make_engine("runpod_comfy", available=False)
    router = EngineRouter(replicate=replicate, runpod=runpod)

    chosen = await router.select("auto")

    assert chosen is replicate
    runpod.is_available.assert_awaited_once()


@pytest.mark.anyio
async def test_mode_seedance_returns_seedance_engine():
    seed = _make_engine("replicate_seedance", available=True)
    router = EngineRouter(seedance=seed)
    chosen = await router.select("seedance")
    assert chosen is seed
