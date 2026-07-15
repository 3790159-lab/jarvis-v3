"""X-API-Key attachment tests for the remaining in-process backend consumers.

Each of these self-calls the gated :8010 backend over loopback and must attach
the internal key, or it 401s under the enforce auth middleware. All use
urllib.request except scheduler (requests). One patch point per HTTP library.
"""
import urllib.request

import pytest


class _FakeResp:
    """Universal fake: works as a direct return AND as a context manager,
    exposes .read() and .status for the various call styles."""

    status = 200

    def __init__(self, body: bytes = b'{"answer":"ok","path":"p"}'):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def capture_urllib(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def _key(captured):
    return captured["req"].get_header("X-api-key")


def test_mcp_server_call_backend_attaches_key(capture_urllib):
    import app.services.mcp_server as mcp
    mcp._call_backend("/api/jarvis/tools/internet/research", {"query": "x"})
    assert _key(capture_urllib) == "jvi_secret"


def test_agent_health_mesh_attaches_key(capture_urllib):
    import app.services.agent_health_mesh as ahm
    ahm._check_http_agent(
        "smart_table", {"endpoint": "/api/jarvis/telegram-tools/internet-table"}
    )
    assert _key(capture_urllib) == "jvi_secret"


def test_result_synthesizer_llm_attaches_key(capture_urllib):
    import app.services.result_synthesizer as rs
    rs._llm_synthesize("query", "context")
    assert _key(capture_urllib) == "jvi_secret"


def test_cross_service_coordinator_obsidian_attaches_key(capture_urllib):
    import app.services.cross_service_coordinator as csc
    csc._save_dish_to_obsidian("Pizza", [], "caption", "#tags")
    assert _key(capture_urllib) == "jvi_secret"


def test_daily_recap_obsidian_attaches_key(capture_urllib, monkeypatch):
    import app.services.daily_recap as dr
    monkeypatch.setattr(dr, "_format_recap_as_markdown", lambda recap: "content")
    dr.save_recap_to_obsidian({"date": "2026-07-16"})
    assert _key(capture_urllib) == "jvi_secret"


def test_trend_analyzer_obsidian_attaches_key(capture_urllib, monkeypatch):
    import app.services.trend_analyzer as ta
    monkeypatch.setattr(ta, "_format_insights_as_markdown", lambda insights: "content")
    ta.save_insights_to_obsidian({"date": "2026-07-16"})
    assert _key(capture_urllib) == "jvi_secret"


def test_dashboard_run_research_attaches_key(capture_urllib):
    import app.routers.jarvis_dashboard_router as dash
    dash._run_research("test query")
    assert _key(capture_urllib) == "jvi_secret"


def test_scheduler_research_attaches_key(monkeypatch):
    import requests
    import app.services.scheduler as sched

    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    captured = {}

    class _RResp:
        def json(self):
            return {"answer": "ok"}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        return _RResp()

    monkeypatch.setattr(requests, "post", fake_post)

    s = sched.JarvisScheduler(send_fn=lambda cid, txt: None)
    s._execute_task(
        {"task_id": "t1", "action": "research", "params": {"query": "x"}, "chat_id": "123"}
    )
    assert captured["headers"].get("X-API-Key") == "jvi_secret"
