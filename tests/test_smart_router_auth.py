"""Auth + loud-failure tests for smart_router's agent-mesh execution.

Two independent concerns, both live under MODE=enforce:
  1. _call_agent / synthesize_results self-call the gated :8010 backend over
     loopback HTTP and must attach the internal X-API-Key, else every agent
     step 401s.
  2. A total execution failure must be LOUD: execute_plan must raise (so the
     caller's fallback engages) instead of silently synthesizing a "done" plan
     out of all-errored steps.
"""
import urllib.request

import pytest

import app.services.smart_router as sr


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def test_call_agent_attaches_internal_key(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    monkeypatch.setattr(
        sr, "get_agent",
        lambda aid: {"endpoint": "/api/jarvis/tools/internet/research", "label": "IR"},
    )
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResp(b'{"answer":"ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sr._call_agent("internet_research", "test query")

    # urllib capitalises header keys: "X-API-Key" -> "X-api-key".
    assert captured["req"].get_header("X-api-key") == "jvi_secret"


def test_call_agent_no_key_sends_no_header(monkeypatch):
    # Graceful degradation: keyless env (MODE=off/canary) must behave as before.
    monkeypatch.delenv("JARVIS_INTERNAL_API_KEY", raising=False)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)
    monkeypatch.setattr(
        sr, "get_agent",
        lambda aid: {"endpoint": "/api/jarvis/tools/internet/research", "label": "IR"},
    )
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResp(b'{"answer":"ok"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    sr._call_agent("internet_research", "q")
    assert captured["req"].get_header("X-api-key") is None


def test_synthesize_results_attaches_internal_key(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResp(b'{"answer":"synthesized"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # 2+ results triggers the LLM-synthesis HTTP path.
    out = sr.synthesize_results(
        "сравни",
        [
            {"agent": "internet_research", "result": {"answer": "x"}},
            {"agent": "smart_table", "result": {"answer": "y"}},
        ],
    )
    assert out == "synthesized"
    assert captured["req"].get_header("X-api-key") == "jvi_secret"


def test_execute_plan_raises_when_all_agent_calls_fail(monkeypatch):
    # Every agent call fails at the transport layer (e.g. 401 under enforce).
    monkeypatch.setattr(
        sr, "_call_agent",
        lambda aid, q: {"_error": "HTTP Error 401: Unauthorized"},
    )
    plan = sr.build_execution_plan("сравни FAL и Replicate", ["internet_research"])
    assert plan.steps  # sanity: the plan actually has work to do

    with pytest.raises(sr.MeshExecutionError):
        sr.execute_plan(plan)


def test_execute_plan_does_not_raise_when_a_step_succeeds(monkeypatch):
    # At least one good result → synthesize normally, never raise.
    monkeypatch.setattr(sr, "_call_agent", lambda aid, q: {"answer": "real answer"})
    plan = sr.build_execution_plan("расскажи про X", ["internet_research"])

    result = sr.execute_plan(plan)  # must NOT raise
    assert result["plan_result"]
