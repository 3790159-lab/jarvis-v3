# -*- coding: utf-8 -*-
"""Phase-4 Step 2.7 — production-hardening the unified LLM router.

Covers the four hardening items:
  1. conversation-history persistence (per-chat, capped at N) threaded into
     ``route_message`` by the bot bridge;
  2. retry/backoff around ``messages.create`` — transient errors (429 / 5xx /
     timeout) retry; 4xx/auth do not; exhaustion degrades to a graceful error
     RouterResponse instead of crashing;
  3. backend wiring — the bridge passes ``persona_generate_fn`` (explicit stub)
     and ``stats_fn`` (real cost formatter) into ``register_default_tools``;
  4. pricing is covered by the existing ``test_router_records_cost_per_call``
     (MODEL_PRICING already matches the catalog — see report).

Router-level tests drive a fake Anthropic client (no network). Bridge-level
tests load the bot module fresh and patch the router seam, like the other
``tests/test_bot_*_integration`` modules.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.router import LLMRouter, RouterResponse
from app.services.unified.llm_router.tool_registry import ToolContext, ToolRegistry


# ════════════════════════════════════════════════════════════════════════════
# Router-level: retry / backoff (item 2)
# ════════════════════════════════════════════════════════════════════════════


class _FakeUsage:
    def __init__(self, i: int = 10, o: int = 5) -> None:
        self.input_tokens = i
        self.output_tokens = o


class _FakeText:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeText(text)]
        self.stop_reason = "end_turn"
        self.usage = _FakeUsage()


class _RateLimit(Exception):
    """Mimics anthropic.RateLimitError (carries status_code 429)."""

    def __init__(self) -> None:
        super().__init__("rate limited")
        self.status_code = 429


class _ServerError(Exception):
    def __init__(self) -> None:
        super().__init__("upstream 503")
        self.status_code = 503


class _BadRequest(Exception):
    """Mimics a 4xx client error — must NOT be retried."""

    def __init__(self) -> None:
        super().__init__("bad request")
        self.status_code = 400


class APITimeoutError(Exception):
    """Name matches the anthropic class; no status_code (network-level)."""


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


def _ctx() -> ToolContext:
    return ToolContext(user_id=222, username="vasya", chat_id="222", conversation_state={})


def _router(client, **kw) -> LLMRouter:
    kw.setdefault("model", "claude-sonnet-4-6")
    kw.setdefault("sleep_fn", lambda *_a, **_k: None)  # no real backoff sleeps
    return LLMRouter(client, ToolRegistry(), **kw)


def test_router_retry_transient_then_succeeds():
    client = _ScriptedClient([_RateLimit(), _ServerError(), _FakeMessage("готово")])
    resp = asyncio.run(_router(client, max_retries=3).route_message("привет", _ctx()))
    assert resp.error == ""
    assert resp.text == "готово"
    assert client.messages.calls == 3  # two retries, then success


def test_router_retry_transient_by_type_name():
    # APITimeoutError has no status_code — classified transient by class name.
    client = _ScriptedClient([APITimeoutError("slow"), _FakeMessage("ok")])
    resp = asyncio.run(_router(client).route_message("x", _ctx()))
    assert resp.text == "ok"
    assert client.messages.calls == 2


def test_router_retry_exhausted_returns_graceful_error():
    client = _ScriptedClient([_RateLimit(), _RateLimit(), _RateLimit()])
    resp = asyncio.run(_router(client, max_retries=3).route_message("привет", _ctx()))
    assert resp.error  # graceful error result, not a raised exception
    assert resp.text == ""
    assert client.messages.calls == 3  # capped at max_retries


def test_router_retry_does_not_retry_client_error():
    client = _ScriptedClient([_BadRequest()])
    resp = asyncio.run(_router(client, max_retries=3).route_message("привет", _ctx()))
    assert resp.error  # surfaced as graceful error, but...
    assert client.messages.calls == 1  # ...4xx is NOT retried


# ════════════════════════════════════════════════════════════════════════════
# Bridge-level: conversation history (item 1) + backend wiring (item 3)
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222")
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")


def _get_bot():
    mod_name = f"_test_router_hard_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class _RecordingRouter:
    """Captures the conversation_history handed to each route_message call."""

    def __init__(self, response=None) -> None:
        self.histories: list = []
        self._response = response

    async def route_message(self, text, context, conversation_history=None):
        self.histories.append(list(conversation_history or []))
        return self._response or RouterResponse(text=f"ответ:{text}")


def _msg(uid=222):
    return {"from": {"id": uid, "username": "u"}, "chat": {"id": uid}}


def test_router_history_first_call_empty_then_accumulates(monkeypatch):
    mod = _get_bot()
    fake = _RecordingRouter()
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    mod._run_router("222", "привет", _msg())
    assert fake.histories[0] == []  # cold start — no history

    mod._run_router("222", "как дела", _msg())
    assert fake.histories[1] == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "ответ:привет"},
    ]


def test_router_history_trimmed_to_max(monkeypatch):
    mod = _get_bot()
    fake = _RecordingRouter()
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    for i in range(12):
        mod._run_router("222", f"m{i}", _msg())

    stored = mod._router_history_get("222")
    assert len(stored) <= mod._ROUTER_HISTORY_MAX
    # Stored history stays a valid alternating prefix: starts with a user turn.
    assert stored[0]["role"] == "user"
    # The last call never saw more than the cap.
    assert len(fake.histories[-1]) <= mod._ROUTER_HISTORY_MAX


def test_router_history_isolated_per_chat(monkeypatch):
    mod = _get_bot()
    fake = _RecordingRouter()
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    mod._run_router("111", "a", _msg(111))
    mod._run_router("222", "b", _msg(222))

    assert mod._router_history_get("111") == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "ответ:a"},
    ]
    assert mod._router_history_get("222") == [
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "ответ:b"},
    ]


def test_router_error_response_falls_back_and_is_not_recorded(monkeypatch):
    mod = _get_bot()
    fake = _RecordingRouter(response=RouterResponse(error="boom"))
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    out = mod._run_router("222", "x", _msg())

    assert out is None  # error → fall back to the legacy dispatcher
    assert mod._router_history_get("222") == []  # a failed turn isn't persisted


# ── backend wiring (item 3) ──────────────────────────────────────────────────


def test_stats_backend_returns_real_formatter(monkeypatch):
    mod = _get_bot()
    monkeypatch.setattr(
        mod._cost, "format_my_stats_message", lambda uid, uname: f"STATS:{uid}:{uname}"
    )
    assert mod._router_stats_backend(222, "vasya") == "STATS:222:vasya"


def test_persona_backend_is_explicit_stub():
    mod = _get_bot()
    with pytest.raises(Exception) as ei:
        mod._persona_generate_backend("p1", "scene", 1)
    assert "не подключ" in str(ei.value).lower()


def test_backends_wired_into_register_default_tools(monkeypatch):
    mod = _get_bot()
    captured: dict = {}

    def _spy(registry, *, dispatch_fn=None, set_quality_fn=None,
             persona_generate_fn=None, persona_exists_fn=None, stats_fn=None,
             video_swap_dispatch_fn=None, voice_synthesize_fn=None,
             voice_send_fn=None):
        captured.update(
            dispatch_fn=dispatch_fn,
            set_quality_fn=set_quality_fn,
            persona_generate_fn=persona_generate_fn,
            stats_fn=stats_fn,
            video_swap_dispatch_fn=video_swap_dispatch_fn,
            voice_synthesize_fn=voice_synthesize_fn,
            voice_send_fn=voice_send_fn,
        )
        return registry

    monkeypatch.setattr(
        "app.services.unified.llm_router.tools.register_default_tools", _spy
    )
    monkeypatch.setattr(
        "app.services.unified.llm_router.llm_client.build_anthropic_client",
        lambda *a, **k: object(),
    )
    mod._ROUTER_SINGLETON = None
    mod._ROUTER_BUILD_FAILED = False

    router = mod._build_router()

    assert router is not None
    assert captured["persona_generate_fn"] is mod._persona_generate_backend
    assert captured["stats_fn"] is mod._router_stats_backend
    assert captured["dispatch_fn"] is mod._swapbatch_dispatch
    assert captured["video_swap_dispatch_fn"] is mod._video_face_swap_dispatch
    assert captured["voice_synthesize_fn"] is mod._voice_synthesize
    assert captured["voice_send_fn"] is mod._router_voice_send
