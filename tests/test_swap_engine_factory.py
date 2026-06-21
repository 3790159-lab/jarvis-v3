import importlib

import pytest


def _factory():
    mod = importlib.import_module(
        "app.services.block_m2_face_swap.engines.factory"
    )
    return importlib.reload(mod)


def test_default_is_lucataco(monkeypatch):
    monkeypatch.delenv("SWAP_ENGINE", raising=False)
    f = _factory()
    assert f.get_swap_cost_per_photo() == pytest.approx(0.005)
    assert f.get_swap_cold_start_usd() == pytest.approx(0.0)


def test_env_selects_runpod(monkeypatch):
    monkeypatch.setenv("SWAP_ENGINE", "runpod")
    f = _factory()
    assert f.get_swap_cost_per_photo() == pytest.approx(0.02)
    assert f.get_swap_cold_start_usd() == pytest.approx(0.05)


def test_unknown_engine_raises(monkeypatch):
    monkeypatch.setenv("SWAP_ENGINE", "bogus")
    f = _factory()
    with pytest.raises(ValueError):
        f.get_swap_engine()


def test_get_swap_engine_lucataco(monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "fake")
    monkeypatch.delenv("SWAP_ENGINE", raising=False)
    f = _factory()
    engine = f.get_swap_engine()
    from app.services.block_m2_face_swap.engines.base import SwapEngine
    assert isinstance(engine, SwapEngine)
    assert engine.name == "lucataco"
