# -*- coding: utf-8 -*-
"""Router file-context awareness.

Bugfix: the LLM router was blind to ``state["last_uploaded_file"]`` — it only
saw the user text + plain-text history, so Claude reasoned "no file attached"
and answered in plain text instead of calling ``answer_about_file`` (logged as
``tools=[]``). The fix threads a per-message ``extra_context`` hint into
``route_message`` (appended to the system prompt for that call only) and the
bot bridge derives that hint from the uploaded-file state.

Router-level tests drive a fake Anthropic client (no network). Bridge-level
tests load the bot module fresh and patch the router seam, like the other
``tests/test_bot_*_integration`` / ``test_router_hardening`` modules.
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

from app.services.unified.llm_router.router import (
    _DEFAULT_SYSTEM_PROMPT,
    LLMRouter,
    RouterResponse,
)
from app.services.unified.llm_router.tool_registry import ToolContext, ToolRegistry


# ════════════════════════════════════════════════════════════════════════════
# Router-level: extra_context is appended to the system prompt for that call
# ════════════════════════════════════════════════════════════════════════════


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5


class _FakeText:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str = "ok") -> None:
        self.content = [_FakeText(text)]
        self.stop_reason = "end_turn"
        self.usage = _FakeUsage()


class _CapturingMessages:
    """Records the kwargs of each ``create`` call (so we can inspect system)."""

    def __init__(self) -> None:
        self.calls: list = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage("готово")


class _CapturingClient:
    def __init__(self) -> None:
        self.messages = _CapturingMessages()


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def _router(client) -> LLMRouter:
    return LLMRouter(client, ToolRegistry(), model="claude-sonnet-4-6")


def test_extra_context_appended_to_system_prompt():
    client = _CapturingClient()
    hint = "📎 Контекст: файл 'договор.pdf' загружен."
    asyncio.run(_router(client).route_message("что в файле?", _ctx(), extra_context=hint))
    system = client.messages.calls[0]["system"]
    assert hint in system
    # the base prompt is preserved, the hint is additive
    assert _DEFAULT_SYSTEM_PROMPT in system


def test_no_extra_context_leaves_system_prompt_unchanged():
    client = _CapturingClient()
    asyncio.run(_router(client).route_message("привет", _ctx()))
    assert client.messages.calls[0]["system"] == _DEFAULT_SYSTEM_PROMPT


def test_empty_extra_context_leaves_system_prompt_unchanged():
    client = _CapturingClient()
    asyncio.run(_router(client).route_message("привет", _ctx(), extra_context=""))
    assert client.messages.calls[0]["system"] == _DEFAULT_SYSTEM_PROMPT


# ════════════════════════════════════════════════════════════════════════════
# Bridge-level: the bot derives the hint from last_uploaded_file state
# ════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222")
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")


def _get_bot():
    mod_name = f"_test_router_filectx_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class _RecordingRouter:
    """Captures the extra_context handed to each route_message call."""

    def __init__(self) -> None:
        self.extras: list = []

    async def route_message(self, text, context, conversation_history=None, extra_context=None):
        self.extras.append(extra_context)
        return RouterResponse(text=f"ответ:{text}")


def _msg(uid=222):
    return {"from": {"id": uid, "username": "u"}, "chat": {"id": uid}}


def test_file_context_hint_present_when_file_uploaded():
    mod = _get_bot()
    state = {"last_uploaded_file": {"filename": "договор.pdf", "mime_type": "application/pdf"}}
    hint = mod._router_file_context_hint(state)
    assert hint
    assert "договор.pdf" in hint
    assert "answer_about_file" in hint


def test_file_context_hint_none_when_no_file():
    mod = _get_bot()
    assert mod._router_file_context_hint({}) is None
    assert mod._router_file_context_hint({"last_uploaded_file": None}) is None


def test_run_router_passes_file_hint_to_route_message(monkeypatch):
    mod = _get_bot()
    fake = _RecordingRouter()
    monkeypatch.setattr(mod, "_build_router", lambda: fake)
    monkeypatch.setattr(
        mod,
        "load_state",
        lambda: {"last_uploaded_file": {"filename": "счет.xlsx", "mime_type": "x"}},
    )

    mod._run_router("222", "какая сумма?", _msg())

    assert fake.extras[0] is not None
    assert "счет.xlsx" in fake.extras[0]


def test_run_router_passes_no_hint_without_file(monkeypatch):
    mod = _get_bot()
    fake = _RecordingRouter()
    monkeypatch.setattr(mod, "_build_router", lambda: fake)
    monkeypatch.setattr(mod, "load_state", lambda: {})

    mod._run_router("222", "привет", _msg())

    assert fake.extras[0] is None
