# -*- coding: utf-8 -*-
"""Integration: plain text routes through the LLM router; commands don't.

Same fresh-module-load harness as ``tests/test_bot_cost_commands_integration``.
The router itself is faked via ``_build_router`` (no Anthropic calls) and the
legacy ``handle`` dispatcher is patched so we can assert exactly which path a
message takes. Backward compatibility is the contract under test: ``/commands``
and a disabled router must still reach ``handle``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.router import RouterResponse
from app.services.unified.llm_router.tool_registry import ToolResult


def _get_mod():
    mod_name = f"_test_router_bot_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(user_id: int, text: str, username: str | None = "tester") -> dict:
    msg = {"from": {"id": user_id}, "chat": {"id": user_id}, "text": text}
    if username is not None:
        msg["from"]["username"] = username
    return {"update_id": 1, "message": msg}


class _FakeRouter:
    def __init__(self, response: RouterResponse) -> None:
        self.response = response
        self.calls: list = []

    async def route_message(self, text, context, conversation_history=None):
        self.calls.append((text, context))
        return self.response


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    # Whitelist the test user and isolate audit/cost state to tmp.
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222")
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")


def test_command_message_uses_legacy_dispatcher(monkeypatch):
    mod = _get_mod()
    handle_calls: list = []
    build_calls: list = []
    fake = _FakeRouter(RouterResponse(text="should not be used"))

    monkeypatch.setattr(mod, "handle", lambda cid, txt: handle_calls.append((str(cid), txt)))
    monkeypatch.setattr(mod, "_build_router", lambda: build_calls.append(1) or fake)

    upd = _text_update(222, "/swapbatch_source")
    with patch.object(mod, "send", lambda *a, **k: None):
        mod.process_update(upd)

    assert handle_calls == [("222", "/swapbatch_source")]
    # A command must never even construct or invoke the router.
    assert build_calls == []
    assert fake.calls == []


def test_text_message_routes_through_llm(monkeypatch):
    mod = _get_mod()
    handle_calls: list = []
    fake = _FakeRouter(RouterResponse(text="Готово!"))

    monkeypatch.setattr(mod, "handle", lambda cid, txt: handle_calls.append((str(cid), txt)))
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    sent: list = []
    upd = _text_update(222, "сделай swap этих фото")
    with patch.object(mod, "send", lambda cid, txt, *a, **k: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert fake.calls, "router should have been invoked for plain text"
    assert fake.calls[0][0] == "сделай swap этих фото"
    # Identity propagated into the ToolContext.
    assert fake.calls[0][1].user_id == 222
    # Legacy dispatcher was NOT used for plain text.
    assert handle_calls == []
    assert any("Готово!" in t for _, t in sent)


def test_router_disabled_falls_back(monkeypatch):
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")
    mod = _get_mod()
    handle_calls: list = []
    build_calls: list = []

    monkeypatch.setattr(mod, "handle", lambda cid, txt: handle_calls.append((str(cid), txt)))
    monkeypatch.setattr(mod, "_build_router", lambda: build_calls.append(1) or None)

    upd = _text_update(222, "привет, как дела")
    with patch.object(mod, "send", lambda *a, **k: None):
        mod.process_update(upd)

    # Disabled → legacy handle runs and the router is never built.
    assert handle_calls == [("222", "привет, как дела")]
    assert build_calls == []


def test_router_response_renders_correctly_in_telegram(monkeypatch):
    mod = _get_mod()
    resp = RouterResponse(
        text="Вот фото",
        media=[ToolResult.photo("https://img.example/1.png", "персона alice")],
    )
    fake = _FakeRouter(resp)

    monkeypatch.setattr(mod, "handle", lambda cid, txt: None)
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    sent: list = []
    photos: list = []
    upd = _text_update(222, "покажи фото alice")
    with patch.object(mod, "send", lambda cid, txt, *a, **k: sent.append((str(cid), txt))):
        with patch.object(
            mod,
            "_send_photo_url",
            lambda cid, url, caption="": photos.append((str(cid), url, caption)),
        ):
            mod.process_update(upd)

    assert any("Вот фото" in t for _, t in sent)
    assert photos == [("222", "https://img.example/1.png", "персона alice")]


def test_router_logs_tools_used(monkeypatch, capsys):
    # Observability: a successful router turn must log which tool(s) it used so
    # operators can see routing decisions (there is no separate bot log file).
    mod = _get_mod()
    fake = _FakeRouter(RouterResponse(text="Готово!", tools_used=["web_research"]))

    monkeypatch.setattr(mod, "handle", lambda cid, txt: None)
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    upd = _text_update(222, "сделай ресёрч по ценам на GPU")
    with patch.object(mod, "send", lambda *a, **k: None):
        mod.process_update(upd)

    out = capsys.readouterr().out
    assert "[router]" in out
    assert "web_research" in out


def test_router_graceful_error_logs_fallback(monkeypatch, capsys):
    # A graceful router error (e.g. API failure after retries) silently fell
    # back to legacy before — now it must be visible in the log, otherwise a
    # smoke test cannot distinguish "router handled it" from "router gave up".
    mod = _get_mod()
    handle_calls: list = []
    fake = _FakeRouter(RouterResponse(error="API down after retries"))

    monkeypatch.setattr(mod, "handle", lambda cid, txt: handle_calls.append((str(cid), txt)))
    monkeypatch.setattr(mod, "_build_router", lambda: fake)

    upd = _text_update(222, "сделай ресёрч по ценам на GPU")
    with patch.object(mod, "send", lambda *a, **k: None):
        mod.process_update(upd)

    out = capsys.readouterr().out
    assert "[router]" in out
    assert "fallback" in out.lower() or "legacy" in out.lower()
    # the legacy dispatcher actually handled the message
    assert handle_calls == [("222", "сделай ресёрч по ценам на GPU")]
