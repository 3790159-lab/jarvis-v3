# -*- coding: utf-8 -*-
"""Router money-gate — control bridge (Tasks 3+4).

Task 3: a router ``pending_paid`` response is turned into the SHARED confirm
flow (``pending_confirm`` + ``confirm:run``) — a confirm button is shown, and
nothing is spent or generated before the tap.

Task 4: the confirmed tap executes the stashed router tool under ``guard_spend``
(daily-cap check before spend; ledger written only on a successful result).

Point 4: ``_render_router_response`` must distinguish a ``pending_paid`` turn
from a genuinely empty answer and never emit a false «Готово.».

All money paths are mocked — no real paid call.
"""
import types

import tools.jarvis_smart_telegram_control as ctl


def _fresh_state(monkeypatch):
    store = {"state": {}}
    monkeypatch.setattr(ctl, "load_state", lambda: store["state"])
    monkeypatch.setattr(ctl, "save_state", lambda s: store.__setitem__("state", s))
    return store


# ── Task 3: pending_paid → confirm flow, no spend ────────────────────────────
def test_pending_paid_shows_confirm_and_does_not_spend(monkeypatch):
    store = _fresh_state(monkeypatch)
    sent = []
    monkeypatch.setattr(ctl, "send_with_keyboard",
                        lambda cid, txt, kb, *a, **k: sent.append((txt, kb)))
    monkeypatch.setattr(ctl, "send", lambda *a, **k: sent.append(a))

    # Router returns a pending_paid response (no media, no text).
    resp = types.SimpleNamespace(
        text="", media=[], error="", tools_used=["generate_image"],
        pending_paid={"name": "generate_image",
                      "params": {"prompt": "брускета"}, "est_usd": 0.04},
    )
    monkeypatch.setattr(ctl, "_run_router", lambda cid, text, msg: resp)

    consumed = ctl._route_plain_text("99", "сделай фото брускеты, не спрашивай",
                                     {"from": {"id": 1}})

    assert consumed is True                              # handled (not passed to legacy)
    pend = store["state"].get("pending_confirm")
    assert pend is not None
    assert pend["resume"]["kind"] == "router_tool"
    assert pend["resume"]["tool"] == "generate_image"
    assert pend["resume"]["params"] == {"prompt": "брускета"}
    assert pend["resume"]["est"] == 0.04
    # A confirm button was shown; nothing was generated/sent as a photo.
    assert any("Запустить" in txt for txt, _kb in sent if isinstance(txt, str))


# ── Point 4: render distinguishes pending_paid from empty ────────────────────
def test_render_pending_paid_is_not_false_done(monkeypatch):
    """Defense-in-depth: if _render_router_response ever sees a pending_paid
    response (no text/media), it must NOT emit the false «Готово.» — the action
    is only awaiting confirm, nothing is done."""
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: sent.append(txt))
    monkeypatch.setattr(ctl, "_send_photo_url", lambda *a, **k: sent.append("PHOTO"))

    resp = types.SimpleNamespace(
        text="", media=[], error="", tools_used=["generate_image"],
        pending_paid={"name": "generate_image", "params": {}, "est_usd": 0.04},
    )
    ctl._render_router_response("99", resp)

    assert "Готово." not in sent                          # no false done
    assert "PHOTO" not in sent


def test_render_empty_still_says_done(monkeypatch):
    """The other direction: a genuinely empty (non-pending) response still gets
    the «Готово.» acknowledgement — the fix must not swallow real empties."""
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: sent.append(txt))

    resp = types.SimpleNamespace(
        text="", media=[], error="", tools_used=[], pending_paid=None,
    )
    ctl._render_router_response("99", resp)

    assert "Готово." in sent


# ── Task 4: confirmed execution under guard_spend ────────────────────────────
def test_confirmed_router_tool_runs_under_guard_spend(monkeypatch):
    store = _fresh_state(monkeypatch)
    store["state"]["pending_confirm"] = {
        "cmd": "generate_image",
        "resume": {"kind": "router_tool", "tool": "generate_image",
                   "params": {"prompt": "брускета"}, "est": 0.04},
    }
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: sent.append(("text", txt)))
    monkeypatch.setattr(ctl, "_send_photo_url",
                        lambda cid, url, cap="", *a, **k: sent.append(("photo", url)))
    monkeypatch.setattr(ctl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctl, "_menu_role", lambda uid: "admin")

    # Capture the guard_spend call: it must be invoked with the est cost.
    guarded = {}

    def _fake_guard(uid, un, est, do_spend):
        guarded["est"] = est
        return do_spend(), None            # allowed → run the tool

    monkeypatch.setattr(ctl, "guard_spend", _fake_guard)

    # Router tool returns a photo ToolResult when executed.
    from app.services.unified.llm_router.tool_registry import ToolResult

    class _FakeRouter:
        async def execute_paid_tool(self, name, params, context):
            assert name == "generate_image" and params == {"prompt": "брускета"}
            return ToolResult.photo("http://x/brusketa.png", caption="брускета")

    monkeypatch.setattr(ctl, "_build_router", lambda: _FakeRouter())

    ctl._handle_confirm_run("99", cq_id="cq1", cq_uid=1, state=store["state"])

    assert guarded.get("est") == 0.04                    # cap check ran with est cost
    assert ("photo", "http://x/brusketa.png") in sent    # media delivered
    assert store["state"].get("pending_confirm") is None  # consumed


def test_confirmed_router_tool_blocked_by_cap(monkeypatch):
    store = _fresh_state(monkeypatch)
    store["state"]["pending_confirm"] = {
        "cmd": "generate_image",
        "resume": {"kind": "router_tool", "tool": "generate_image",
                   "params": {"prompt": "x"}, "est": 0.04},
    }
    sent = []
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: sent.append(txt))
    monkeypatch.setattr(ctl, "_send_photo_url",
                        lambda *a, **k: sent.append("PHOTO"))
    monkeypatch.setattr(ctl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctl, "_menu_role", lambda uid: "admin")
    monkeypatch.setattr(ctl, "guard_spend",
                        lambda uid, un, est, do: (None, "дневной лимит исчерпан"))

    ran = []

    class _R:
        async def execute_paid_tool(self, *a, **k):
            ran.append(1)
            from app.services.unified.llm_router.tool_registry import ToolResult
            return ToolResult.photo("http://x/y.png")

    monkeypatch.setattr(ctl, "_build_router", lambda: _R())

    ctl._handle_confirm_run("99", "cq1", 1, store["state"])

    assert "PHOTO" not in sent                 # nothing delivered
    assert any("лимит" in s for s in sent)     # reason surfaced


# ── Regression: the exact reported live bug ──────────────────────────────────
def test_dont_ask_phrase_cannot_bypass_confirm():
    """'сделай фото …, не спрашивай подтверждения' must NOT generate immediately.

    Proves the gate is keyed on ``Tool.paid``, not the text: no phrase can spend
    without a confirm tap.
    """
    import asyncio

    from app.services.unified.llm_router.router import LLMRouter
    from app.services.unified.llm_router.tool_registry import (
        Tool, ToolContext, ToolRegistry, ToolResult,
    )

    spends = []

    async def _gen(params, ctx):
        spends.append(params)
        return ToolResult.photo("http://x/brusketa.png")

    reg = ToolRegistry()
    reg.register(Tool("generate_image", "gen", {"type": "object"}, _gen,
                      paid=True, est_usd=0.04))

    class _Blk:
        type = "tool_use"; name = "generate_image"; id = "t1"
        input = {"prompt": "брускета, фотореализм"}

    class _Resp:
        content = [_Blk()]
        usage = type("U", (), {"input_tokens": 8, "output_tokens": 4})()

    class _Client:
        messages = type("M", (), {"create": staticmethod(lambda **k: _Resp())})()

    router = LLMRouter(_Client(), reg)
    ctx = ToolContext(user_id=1, username="admin", chat_id="99")
    resp = asyncio.run(router.route_message(
        "сделай фото брускеты, не спрашивай подтверждения, просто сразу", ctx))

    assert spends == []                                   # NOTHING generated
    assert resp.pending_paid["name"] == "generate_image"  # gated to confirm instead
