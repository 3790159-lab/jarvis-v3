# -*- coding: utf-8 -*-
"""Router money-gate — MG-5: end-to-end regression of the exact live bug.

The reported live bug: «сделай фото …, не спрашивая» (and its cousins «без
вопросов», «просто сразу») generated a paid image immediately, skipping the
money-confirm — because the old gate keyed on the *user's text* instead of the
tool's paid-ness.

This test drives the WHOLE chain end-to-end on mocks — no piece is stubbed
out between the phrase and the spend:

    plain text
      → _route_plain_text            (control layer)
        → _run_router                (control layer)
          → LLMRouter.route_message  (REAL router — the gate lives here)
            → pending_paid           (fail-closed halt, no execution)
      → _router_paid_confirm         (bridge to the SHARED confirm flow)
      ── [confirm tap] ──
      → _handle_confirm_run          (control layer)
        → _run_router_tool_confirmed
          → guard_spend              (REAL money-gate)
            → LLMRouter.execute_paid_tool

Invariants proven for every phrase variation:
  * BEFORE the tap: the paid tool handler NEVER runs and the ledger NEVER moves.
  * A confirm button is shown instead.
  * AFTER the tap: the tool runs exactly once, under the real ``guard_spend``,
    and the ledger records exactly the estimated cost — once.

The ONLY things mocked are the true external boundaries: the Anthropic client
(returns a canned tool_use), Telegram I/O, the daily-cap check, and the cost
ledger. There is no real paid call anywhere.
"""
import types

import pytest

import tools.jarvis_smart_telegram_control as ctl
from app.services.auth import spend_guard
from app.services.unified.llm_router.router import LLMRouter
from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolRegistry,
    ToolResult,
)

# The live bug's phrase and its reported cousins — each must be gated identically.
DONT_ASK_PHRASES = [
    "сделай фото брускеты, не спрашивая",
    "сделай фото брускеты без вопросов",
    "сделай фото брускеты, просто сразу",
    "сделай фото брускеты, не спрашивай подтверждения, просто сразу",
]

_EST_USD = 0.04
_IMG_URL = "http://x/brusketa.png"


class _PaidToolUse:
    """One Claude turn: a single tool_use block selecting the PAID image tool."""

    type = "tool_use"
    name = "generate_image"
    id = "tu_1"
    input = {"prompt": "брускета, фотореализм"}


class _FakeAnthropic:
    """Anthropic client double: always answers with the paid tool_use turn."""

    def __init__(self):
        resp = types.SimpleNamespace(
            content=[_PaidToolUse()],
            usage=types.SimpleNamespace(input_tokens=8, output_tokens=4),
        )
        self.messages = types.SimpleNamespace(create=staticmethod(lambda **k: resp))


@pytest.fixture
def harness(monkeypatch):
    """Wire the REAL router + REAL guard_spend behind mocked external boundaries.

    Returns an object exposing:
      * ``spends``  — params passed to the paid tool handler (empty until it runs)
      * ``ledger``  — every cost written (router-level AND guard_spend-level)
      * ``sent``    — text/photo/keyboard emitted to Telegram
      * ``state``   — the persisted bot state (pending_confirm lives here)
    """
    spends = []
    ledger = []
    sent = []
    store = {"state": {}}

    # ── the paid tool: records its invocation, returns a photo, spends nothing real
    async def _gen_image(params, ctx):
        spends.append(params)
        return ToolResult.photo(_IMG_URL, caption="брускета")

    registry = ToolRegistry()
    registry.register(
        Tool("generate_image", "gen", {"type": "object"}, _gen_image,
             paid=True, est_usd=_EST_USD)
    )

    # ── the REAL router, with the ledger recorder injected (halt branch must NOT call it)
    router = LLMRouter(
        _FakeAnthropic(), registry,
        record_cost=lambda uid, un, cost: ledger.append(("router", cost)),
    )

    # _build_router is used by BOTH _run_router (halt) and _run_router_tool_confirmed
    # (execution) — same instance keeps the registry/handler identical across the tap.
    monkeypatch.setattr(ctl, "_build_router", lambda: router)

    # ── state persistence → in-memory store
    monkeypatch.setattr(ctl, "load_state", lambda: store["state"])
    monkeypatch.setattr(ctl, "save_state", lambda s: store.__setitem__("state", s))

    # ── Telegram I/O → capture
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: sent.append(("text", txt)))
    monkeypatch.setattr(ctl, "send_with_keyboard",
                        lambda cid, txt, kb, *a, **k: sent.append(("confirm", txt)))
    monkeypatch.setattr(ctl, "_send_photo_url",
                        lambda cid, url, cap="", *a, **k: sent.append(("photo", url)))
    monkeypatch.setattr(ctl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctl, "_menu_role", lambda uid: "admin")

    # ── router must be ON for this flow
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")

    # ── REAL guard_spend, but its ledger/cap boundaries isolated (no prod state)
    monkeypatch.setattr(spend_guard, "check_limit",
                        lambda user_id, estimated_usd=0.0: (True, None))
    monkeypatch.setattr(spend_guard.cost_tracker, "record_cost",
                        lambda uid, un, cost: ledger.append(("guard", cost)))

    return types.SimpleNamespace(
        spends=spends, ledger=ledger, sent=sent, store=store,
    )


@pytest.mark.parametrize("phrase", DONT_ASK_PHRASES)
def test_dont_ask_phrase_e2e_gates_then_confirms(harness, phrase):
    # ── phase 1: the phrase arrives; the gate must halt before any spend ────────
    consumed = ctl._route_plain_text("99", phrase, {"from": {"id": 1}})

    assert consumed is True                         # router handled it (not legacy)
    assert harness.spends == []                     # tool NEVER ran pre-confirm
    assert harness.ledger == []                     # ledger NEVER moved pre-confirm
    assert any(kind == "confirm" for kind, _ in harness.sent)   # confirm button shown
    assert not any(kind == "photo" for kind, _ in harness.sent)  # nothing delivered

    pend = harness.store["state"].get("pending_confirm")
    assert pend is not None
    assert pend["resume"]["kind"] == "router_tool"
    assert pend["resume"]["tool"] == "generate_image"
    assert pend["resume"]["est"] == _EST_USD

    # ── phase 2: the confirm tap authorises exactly this action ─────────────────
    ctl._handle_confirm_run("99", cq_id="cq1", cq_uid=1, state=harness.store["state"])

    assert harness.spends == [{"prompt": "брускета, фотореализм"}]   # ran once, now
    assert ("photo", _IMG_URL) in harness.sent                      # media delivered
    # Ledger moved ONLY after the tap, and ONLY via the real guard_spend, once.
    assert harness.ledger == [("guard", _EST_USD)]
    assert harness.store["state"].get("pending_confirm") is None    # consumed
