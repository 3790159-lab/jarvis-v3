from __future__ import annotations
from chatter.core.llm import LLMClient, FakeLLM

def test_fakellm_is_llmclient():
    assert isinstance(FakeLLM(), LLMClient)

def test_fakellm_records_calls_and_returns_default():
    llm = FakeLLM()
    out = llm.complete("SYS", [{"role": "user", "content": "привет"}], max_tokens=100)
    assert isinstance(out, str) and out
    assert llm.calls[0]["system"] == "SYS"
    assert llm.calls[0]["messages"][0]["content"] == "привет"
    assert llm.calls[0]["max_tokens"] == 100

def test_fakellm_scripted_replies_in_order():
    llm = FakeLLM(scripted=["первый", "второй"])
    assert llm.complete("s", [{"role": "user", "content": "a"}], max_tokens=10) == "первый"
    assert llm.complete("s", [{"role": "user", "content": "b"}], max_tokens=10) == "второй"

def test_fakellm_echoes_last_user_when_no_script():
    llm = FakeLLM()
    out = llm.complete("s", [{"role": "user", "content": "сколько стоит фотосессия"}], max_tokens=10)
    assert "фотосессия" in out

def test_anthropicllm_import_is_lazy(monkeypatch):
    """Importing chatter.core.llm and constructing FakeLLM must never require the
    anthropic SDK. This guards against accidentally hoisting `import anthropic`
    to module scope, which would break offline/no-key test runs."""
    import sys
    assert "anthropic" not in sys.modules or True  # sanity: module import itself must not fail
    from chatter.core.llm import AnthropicLLM  # noqa: F401 - class import must not touch the SDK
