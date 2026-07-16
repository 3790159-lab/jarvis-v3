from __future__ import annotations
import subprocess
import sys
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

def test_importing_llm_module_does_not_import_anthropic():
    """Importing chatter.core.llm (and referencing AnthropicLLM as a class) must
    never pull in the anthropic SDK. Run in a fresh subprocess so the check is
    robust regardless of whether `anthropic` happens to be installed or already
    imported by something else in this pytest process."""
    code = (
        "import sys; import chatter.core.llm; "
        "assert 'anthropic' not in sys.modules, 'anthropic imported at module load'"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
