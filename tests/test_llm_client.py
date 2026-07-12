# -*- coding: utf-8 -*-
"""Unit tests for ``llm_client`` — the Anthropic client factory + price config.

This module centralises (a) construction of the Anthropic SDK client (reading
``ANTHROPIC_API_KEY``, degrading to ``None`` when the SDK or key is absent so
the bot can fall back to the legacy dispatcher) and (b) the model id / per-token
pricing that used to live inline in ``router.py``. The anthropic module is
injected so these tests never import the real SDK or make a network call.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router import llm_client


@pytest.fixture(autouse=True)
def _reset_balance_flag():
    """The depleted/alerted flags are module-level globals — isolate every test."""
    llm_client.reset_balance_flag()
    yield
    llm_client.reset_balance_flag()


class _FakeAnthropicModule:
    """Stand-in for the ``anthropic`` package exposing ``Anthropic``."""

    def __init__(self, *, raises: bool = False) -> None:
        self.constructed_with: dict | None = None
        self._raises = raises

    class _Messages:
        def create(self, **kwargs):  # pragma: no cover - not exercised here
            raise NotImplementedError

    class _Client:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key
            self.messages = _FakeAnthropicModule._Messages()

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


# ── credit_balance_too_low fail-closed guard ────────────────────────────────


class _CreditBalanceLowError(Exception):
    """Mimics anthropic.BadRequestError for 'credit balance is too low'."""

    def __init__(self, message: str = "Your credit balance is too low to access "
                                        "the Anthropic API.") -> None:
        super().__init__(message)
        self.status_code = 400


class _ScriptedMessages:
    def __init__(self, script) -> None:
        self._script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _ScriptedClient:
    def __init__(self, script) -> None:
        self.messages = _ScriptedMessages(script)


class _ScriptedAnthropicModule:
    def __init__(self, script) -> None:
        self._script = script
        self.client: _ScriptedClient | None = None

    def Anthropic(self, *, api_key: str):  # noqa: N802 - mirrors SDK surface
        self.client = _ScriptedClient(self._script)
        return self.client


def _guarded_client(monkeypatch, script):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    mod = _ScriptedAnthropicModule(script)
    client = llm_client.build_anthropic_client(anthropic_module=mod)
    assert client is not None
    return client, mod


def test_is_credit_balance_error_detects_400_marker():
    assert llm_client.is_credit_balance_error(_CreditBalanceLowError()) is True


def test_is_credit_balance_error_ignores_other_400s():
    exc = Exception("bad request: missing field")
    exc.status_code = 400
    assert llm_client.is_credit_balance_error(exc) is False


def test_is_credit_balance_error_ignores_non_400_status():
    exc = Exception("rate limited: credit balance is too low")
    exc.status_code = 429
    assert llm_client.is_credit_balance_error(exc) is False


def test_balance_not_depleted_by_default():
    assert llm_client.is_balance_depleted() is False


def test_guarded_client_sets_flag_on_credit_balance_error(monkeypatch):
    client, mod = _guarded_client(monkeypatch, [_CreditBalanceLowError()])
    with pytest.raises(llm_client.CreditBalanceDepletedError):
        client.messages.create(model="x", max_tokens=1, messages=[])
    assert llm_client.is_balance_depleted() is True
    assert mod.client.messages.calls == 1


def test_guarded_client_fails_closed_without_hitting_api(monkeypatch):
    client, mod = _guarded_client(
        monkeypatch, [_CreditBalanceLowError(), "should never be reached"]
    )
    with pytest.raises(llm_client.CreditBalanceDepletedError):
        client.messages.create(model="x", max_tokens=1, messages=[])
    # Second (and any subsequent) call: flag already set → must NOT call the SDK.
    with pytest.raises(llm_client.CreditBalanceDepletedError):
        client.messages.create(model="x", max_tokens=1, messages=[])
    assert mod.client.messages.calls == 1


def test_guarded_client_passes_through_other_errors(monkeypatch):
    rate_limit = Exception("rate limited")
    rate_limit.status_code = 429
    client, mod = _guarded_client(monkeypatch, [rate_limit])
    with pytest.raises(Exception) as exc_info:
        client.messages.create(model="x", max_tokens=1, messages=[])
    assert not isinstance(exc_info.value, llm_client.CreditBalanceDepletedError)
    assert llm_client.is_balance_depleted() is False
    assert mod.client.messages.calls == 1


def test_reset_balance_flag_clears_fail_closed(monkeypatch):
    client, mod = _guarded_client(
        monkeypatch, [_CreditBalanceLowError(), "second call succeeds"]
    )
    with pytest.raises(llm_client.CreditBalanceDepletedError):
        client.messages.create(model="x", max_tokens=1, messages=[])
    assert llm_client.is_balance_depleted() is True

    llm_client.reset_balance_flag()
    assert llm_client.is_balance_depleted() is False

    result = client.messages.create(model="x", max_tokens=1, messages=[])
    assert result == "second call succeeds"
    assert mod.client.messages.calls == 2  # actually reached the API again


def test_mark_balance_depleted_logs_warning_once(caplog):
    caplog.set_level(logging.WARNING)
    first = llm_client.mark_balance_depleted()
    second = llm_client.mark_balance_depleted()
    assert first is True
    assert second is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_should_alert_owner_once_per_episode():
    assert llm_client.should_alert_owner() is True
    assert llm_client.should_alert_owner() is False
    llm_client.reset_balance_flag()
    assert llm_client.should_alert_owner() is True
