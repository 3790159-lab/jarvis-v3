"""Prompt-caching + usage-логгер: контракт AnthropicLLM (спека
2026-07-23-chatter-prompt-caching). SDK мокается через sys.modules — сеть и
ключ не нужны."""
from __future__ import annotations

import inspect
import logging
import sys
import types

import pytest

from chatter.core.llm import AnthropicLLM, FakeLLM, LLMClient


# --- фейковый anthropic SDK ---------------------------------------------------
class _FakeUsage:
    def __init__(self, **kw):
        self.input_tokens = kw.get("input_tokens", 100)
        self.output_tokens = kw.get("output_tokens", 20)
        self.cache_read_input_tokens = kw.get("cache_read_input_tokens", 0)
        self.cache_creation_input_tokens = kw.get("cache_creation_input_tokens", 0)


class _FakeBlock:
    type = "text"
    text = "ок"


class _FakeResponse:
    def __init__(self, usage=None):
        self.content = [_FakeBlock()]
        self.usage = usage or _FakeUsage()


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropicClient:
    last: "_FakeAnthropicClient" = None

    def __init__(self):
        _FakeAnthropicClient.last = self
        self.messages = _FakeMessages(_FakeResponse())


@pytest.fixture()
def fake_sdk(monkeypatch):
    mod = types.ModuleType("anthropic")
    mod.Anthropic = _FakeAnthropicClient
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


def _call(llm, **kw):
    return llm.complete("SYS", [{"role": "user", "content": "привет"}],
                        max_tokens=50, **kw)


# --- DEV-19: сигнатуры фейка и боевого клиента обязаны совпадать -------------
def test_fake_and_real_complete_signatures_match():
    assert (inspect.signature(FakeLLM.complete)
            == inspect.signature(AnthropicLLM.complete))
    assert (inspect.signature(LLMClient.complete)
            == inspect.signature(AnthropicLLM.complete))


# --- кэшируемый префикс -------------------------------------------------------
def test_system_sent_as_single_block_with_cache_control(fake_sdk):
    llm = AnthropicLLM("claude-sonnet-5")
    out = _call(llm)
    assert out == "ок"
    sent = _FakeAnthropicClient.last.messages.calls[0]
    assert sent["system"] == [{
        "type": "text", "text": "SYS",
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }]


def test_uncached_suffix_is_second_block_without_cache_control(fake_sdk):
    llm = AnthropicLLM("claude-sonnet-5")
    _call(llm, uncached_suffix="РАЗОВАЯ ЗАМЕТКА")
    sent = _FakeAnthropicClient.last.messages.calls[0]
    assert len(sent["system"]) == 2
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert sent["system"][1] == {"type": "text", "text": "РАЗОВАЯ ЗАМЕТКА"}


def test_no_thinking_still_disables_thinking(fake_sdk):
    llm = AnthropicLLM("claude-sonnet-5")
    _call(llm, no_thinking=True)
    sent = _FakeAnthropicClient.last.messages.calls[0]
    assert sent["thinking"] == {"type": "disabled"}


# --- usage-логгер -------------------------------------------------------------
def test_usage_sink_receives_model_tag_and_token_fields(fake_sdk):
    records = []
    llm = AnthropicLLM("claude-sonnet-5", usage_sink=records.append)
    _call(llm, tag="brain")
    _FakeAnthropicClient.last.messages._response = _FakeResponse(
        _FakeUsage(input_tokens=7, cache_read_input_tokens=5000,
                   cache_creation_input_tokens=0, output_tokens=33))
    _call(llm, tag="classifier")
    assert records[0]["tag"] == "brain"
    assert records[0]["model"] == "claude-sonnet-5"
    assert records[0]["input_tokens"] == 100
    assert records[1] == {
        "tag": "classifier", "model": "claude-sonnet-5",
        "input_tokens": 7, "output_tokens": 33,
        "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 0,
    }


def test_missing_cache_fields_normalize_to_zero(fake_sdk):
    """Старые модели/моки могут не отдавать cache_*-поля — None не должен
    попадать в БД."""
    records = []
    llm = AnthropicLLM("claude-sonnet-5", usage_sink=records.append)
    usage = _FakeUsage()
    usage.cache_read_input_tokens = None
    usage.cache_creation_input_tokens = None
    _FakeAnthropicClient.last = None
    llm._client.messages._response = _FakeResponse(usage)
    _call(llm, tag="brain")
    assert records[0]["cache_read_input_tokens"] == 0
    assert records[0]["cache_creation_input_tokens"] == 0


def test_usage_sink_failure_does_not_break_reply_but_logs_warning(fake_sdk, caplog):
    def broken_sink(rec):
        raise RuntimeError("диск полон")

    llm = AnthropicLLM("claude-sonnet-5", usage_sink=broken_sink)
    with caplog.at_level(logging.WARNING, logger="chatter.core.llm"):
        out = _call(llm, tag="brain")
    assert out == "ок"  # ответ дошёл
    assert any("usage" in r.message for r in caplog.records)  # но не молча


def test_no_sink_no_crash(fake_sdk):
    llm = AnthropicLLM("claude-sonnet-5")
    assert _call(llm) == "ок"


# --- FakeLLM: контракт для офлайн-тестов -------------------------------------
def test_fakellm_records_suffix_and_tag_and_keeps_system_concat():
    llm = FakeLLM(scripted=["ок"])
    llm.complete("SYS", [{"role": "user", "content": "а"}], max_tokens=10,
                 uncached_suffix="ЗАМЕТКА", tag="brain")
    call = llm.calls[0]
    # наблюдаемый контракт прежний: suffix виден в system (как в реальном
    # запросе он часть system-блоков) + новые поля отдельно
    assert "SYS" in call["system"] and "ЗАМЕТКА" in call["system"]
    assert call["uncached_suffix"] == "ЗАМЕТКА"
    assert call["tag"] == "brain"


def test_fakellm_defaults_unchanged():
    llm = FakeLLM()
    llm.complete("SYS", [{"role": "user", "content": "а"}], max_tokens=10)
    call = llm.calls[0]
    assert call["system"] == "SYS"
    assert call["uncached_suffix"] is None
    assert call["tag"] == ""
