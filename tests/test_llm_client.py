# -*- coding: utf-8 -*-
"""Unit tests for ``llm_client`` — the Anthropic client factory + price config.

This module centralises (a) construction of the Anthropic SDK client (reading
``ANTHROPIC_API_KEY``, degrading to ``None`` when the SDK or key is absent so
the bot can fall back to the legacy dispatcher) and (b) the model id / per-token
pricing that used to live inline in ``router.py``. The anthropic module is
injected so these tests never import the real SDK or make a network call.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router import llm_client


class _FakeAnthropicModule:
    """Stand-in for the ``anthropic`` package exposing ``Anthropic``."""

    def __init__(self, *, raises: bool = False) -> None:
        self.constructed_with: dict | None = None
        self._raises = raises

    class _Client:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

    def Anthropic(self, *, api_key: str):  # noqa: N802 - mirrors SDK surface
        if self._raises:
            raise RuntimeError("sdk construction failed")
        self.constructed_with = {"api_key": api_key}
        return self._Client(api_key=api_key)


# ── build_anthropic_client ───────────────────────────────────────────────────


def test_build_client_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_client.build_anthropic_client(anthropic_module=_FakeAnthropicModule()) is None


def test_build_client_uses_explicit_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mod = _FakeAnthropicModule()
    client = llm_client.build_anthropic_client(api_key="sk-explicit", anthropic_module=mod)
    assert client is not None
    assert client.api_key == "sk-explicit"
    assert mod.constructed_with == {"api_key": "sk-explicit"}


def test_build_client_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    mod = _FakeAnthropicModule()
    client = llm_client.build_anthropic_client(anthropic_module=mod)
    assert client is not None
    assert client.api_key == "sk-from-env"


def test_build_client_returns_none_when_construction_fails(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert (
        llm_client.build_anthropic_client(anthropic_module=_FakeAnthropicModule(raises=True))
        is None
    )


# ── resolve_model ────────────────────────────────────────────────────────────


def test_resolve_model_defaults(monkeypatch):
    monkeypatch.delenv("JARVIS_ROUTER_MODEL", raising=False)
    assert llm_client.resolve_model() == llm_client.DEFAULT_MODEL
    assert llm_client.DEFAULT_MODEL == "claude-sonnet-4-6"


def test_resolve_model_from_env(monkeypatch):
    monkeypatch.setenv("JARVIS_ROUTER_MODEL", "claude-opus-4-8")
    assert llm_client.resolve_model() == "claude-opus-4-8"


def test_resolve_model_blank_env_falls_back(monkeypatch):
    monkeypatch.setenv("JARVIS_ROUTER_MODEL", "   ")
    assert llm_client.resolve_model() == llm_client.DEFAULT_MODEL


# ── compute_cost / pricing ───────────────────────────────────────────────────


def test_compute_cost_sonnet():
    # $3/1M in + $15/1M out → 1000*3e-6 + 500*15e-6 = 0.0105
    assert llm_client.compute_cost("claude-sonnet-4-6", 1000, 500) == pytest.approx(0.0105)


def test_compute_cost_unknown_model_uses_default():
    assert llm_client.compute_cost("totally-unknown", 1000, 500) == pytest.approx(0.0105)


def test_router_reexports_compute_cost():
    # Backward compat: router.compute_cost must keep working after the move.
    from app.services.unified.llm_router import router

    assert router.compute_cost("claude-sonnet-4-6", 1000, 500) == pytest.approx(0.0105)
