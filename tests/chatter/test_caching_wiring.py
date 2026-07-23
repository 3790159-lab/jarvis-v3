"""Wiring кэша и логгера: brain/classifier тегируют вызовы, context_note едет
uncached_suffix-ом (кэш системы не инвалидируется), билдеры LLM пробрасывают
usage_sink (спека 2026-07-23-chatter-prompt-caching)."""
from __future__ import annotations

import sys
import types

from chatter.core.brain import Brain
from chatter.core.classifier import classify
from chatter.core.llm import FakeLLM
from chatter.config.loader import load_config
from tests.chatter.test_loader import _make_client


def _cfg(tmp_path):
    _make_client(tmp_path)
    return load_config(tmp_path, "demo")


def test_brain_tags_calls_and_passes_note_as_uncached_suffix(tmp_path):
    cfg = _cfg(tmp_path)
    llm = FakeLLM(scripted=["ок1", "ок2"])
    brain = Brain(llm, cfg)

    brain.reply([{"role": "user", "text": "привет"}])
    assert llm.calls[0]["tag"] == "brain"
    assert llm.calls[0]["uncached_suffix"] is None

    brain.reply([{"role": "user", "text": "вы тут?"}], context_note="ЗАМЕТКА")
    call = llm.calls[1]
    # стабильная часть системы БЕЗ заметки (кэш жив), заметка — суффиксом
    assert "ЗАМЕТКА" not in call["system"].split("=== КОНТЕКСТ")[0]
    assert "ЗАМЕТКА" in call["uncached_suffix"]
    assert "КОНТЕКСТ ОТВЕТА" in call["uncached_suffix"]


def test_classifier_tags_calls(tmp_path):
    llm = FakeLLM(scripted=['{"escalate": false, "reason": "", "stage_signal": null}'])
    classify(llm, playbook="Веди к брони.", language="ru",
             history=[{"role": "user", "text": "привет"}])
    call = llm.calls[0]
    assert call["tag"] == "classifier"
    assert call["no_thinking"] is True
    assert call["uncached_suffix"] is None


def _fake_sdk(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self):
            captured["client"] = self

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return captured


def test_run_build_llm_passes_sink(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from chatter import run as run_mod
    cfg = _cfg(tmp_path)
    sink = lambda rec: None  # noqa: E731
    llm = run_mod._build_llm(cfg, "real", usage_sink=sink)
    assert llm._usage_sink is sink


def test_telethon_build_llm_passes_sink(tmp_path, monkeypatch):
    _fake_sdk(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from chatter import telethon_run as tr
    cfg = _cfg(tmp_path)
    sink = lambda rec: None  # noqa: E731
    llm = tr._build_llm(cfg, "real", usage_sink=sink)
    assert llm._usage_sink is sink


def test_build_llm_fake_mode_ignores_sink(tmp_path, monkeypatch):
    """Без ключа/real-режима — FakeLLM, sink не обязателен и не ломает."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from chatter import run as run_mod
    cfg = _cfg(tmp_path)
    llm = run_mod._build_llm(cfg, "auto", usage_sink=lambda rec: None)
    assert isinstance(llm, FakeLLM)
