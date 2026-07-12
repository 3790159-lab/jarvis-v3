# -*- coding: utf-8 -*-
"""/suggest_tasks bot wiring (control module). $0, mocks only, no real Anthropic call.

Covers: admin-only-by-omission, the LLM call isolated behind guard_spend (money-
safety mirror of IR-2's _ir2_ask_haiku), honest fallback on guard_spend block,
honest fallback on unparseable LLM reply, and the happy path sending a
formatted top-3.
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_suggest_tasks_is_admin_only_not_friend():
    assert "/suggest_tasks" not in mod.FRIEND_ALLOWED_COMMANDS


# ── v0.2: log-error freshness window defaults to 2 days, env-overridable ────
def test_suggest_tasks_log_since_days_defaults_to_2():
    assert mod._SUGGEST_TASKS_LOG_SINCE_DAYS == 2


def test_suggest_tasks_is_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/suggest_tasks") is True


def test_suggest_tasks_dispatch_calls_llm_under_guard_spend(monkeypatch, tmp_path):
    calls = {"llm": 0, "guard_spend_args": None}

    def _fake_guard_spend(uid, uname, est, do):
        calls["guard_spend_args"] = (uid, uname, est)
        return do(), None

    def _fake_llm(system, messages):
        calls["llm"] += 1
        assert "JSON" in system
        return '[{"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "M"}]'

    monkeypatch.setattr(mod, "guard_spend", _fake_guard_spend)
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _fake_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: {"failed": 1, "passed": 2, "errors": 0})
    monkeypatch.setattr(mod, "_SUGGESTED_TASKS_STATE_PATH", tmp_path / "suggested_tasks_last.json")
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))

    mod._suggest_tasks_dispatch(ADMIN)

    assert calls["llm"] == 1
    assert calls["guard_spend_args"][0] == ADMIN
    assert len(kb_sent) == 1                     # one card per suggestion (here: one)
    text, kb = kb_sent[0]
    assert "T" in text
    assert "🛠" in kb[0][0]["text"]               # tappable run-as-dev_task button
    assert kb[0][0]["callback_data"].startswith("sugtask:run:")


def test_suggest_tasks_dispatch_honest_fallback_when_gate_blocks(monkeypatch):
    seen = {"llm": 0}

    def _blocking_guard_spend(uid, uname, est, do):
        return None, "🚫 лимит исчерпан"

    def _fake_llm(system, messages):
        seen["llm"] += 1
        return "[]"

    monkeypatch.setattr(mod, "guard_spend", _blocking_guard_spend)
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _fake_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._suggest_tasks_dispatch(ADMIN)

    assert seen["llm"] == 0                     # do_spend never ran -> $0
    assert "лимит" in sent["t"]


def test_suggest_tasks_dispatch_honest_fallback_on_garbage_reply(monkeypatch):
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", lambda s, m: "not json")
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._suggest_tasks_dispatch(ADMIN)

    assert "не удалось" in sent["t"].lower()


def test_suggest_tasks_dispatch_error_includes_raw_reply_snippet_for_diagnosis(monkeypatch):
    """(5) битый ответ → сообщение об ошибке должно нести первые 200 симв.
    сырого ответа LLM, иначе живой инцидент (15:57) снова не диагностировать."""
    dirty = "totally not json at all, sorry about that, here is some prose " * 5
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", lambda s, m: dirty)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._suggest_tasks_dispatch(ADMIN)

    assert "не удалось" in sent["t"].lower()
    assert dirty[:100] in sent["t"]


def test_suggest_tasks_ask_llm_uses_raised_max_tokens(monkeypatch):
    """(3) max_tokens=1500 was tight enough to truncate a full top-3 JSON reply
    (draft fields are full /dev_task specs) — must be raised well above that."""
    from app.services.devtask import suggest as sug

    captured = {}

    class _Block:
        type = "text"
        text = "[]"

    class _Resp:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _FakeClient:
        messages = _Messages()

    monkeypatch.setattr(
        "app.services.unified.llm_router.llm_client.build_anthropic_client",
        lambda: _FakeClient(),
    )

    mod._suggest_tasks_ask_llm("sys", [{"role": "user", "content": "x"}])

    assert captured["max_tokens"] == sug.MAX_OUTPUT_TOKENS
    assert captured["max_tokens"] > 1500


def test_suggest_tasks_command_dispatches(monkeypatch):
    fired = {}
    monkeypatch.setattr(mod, "_suggest_tasks_dispatch", lambda cid: fired.setdefault("cid", cid))
    state = {"_paid_confirmed": "/suggest_tasks"}
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)
    assert fired["cid"] == ADMIN


def test_suggest_tasks_command_gated_by_money_gate_without_token(monkeypatch):
    fired = {"dispatch": 0, "confirm": 0}
    monkeypatch.setattr(mod, "_suggest_tasks_dispatch", lambda cid: fired.__setitem__("dispatch", 1))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: fired.__setitem__("confirm", 1))
    state = {}
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)
    assert fired["dispatch"] == 0 and fired["confirm"] == 1


# ── e2e: /suggest_tasks → pending confirm → tap → real dispatch (bug repro) ──
# Reported symptom: money-confirm shows correctly, but tapping [▶️ Запустить]
# replies "Не знаю такую команду" — as if the confirm callback never reaches
# handle_command's "/suggest_tasks" dispatch branch. Unlike the unit tests
# above (which stub handle_command or _suggest_tasks_dispatch away), this test
# drives the REAL handle_command + handle_callback_query + _handle_confirm_run
# + _suggest_tasks_dispatch chain end-to-end — only the network (send/
# send_with_keyboard/answer_callback_query), guard_spend and the LLM call are
# mocked (money-safety: zero real spend, zero real Anthropic calls).
def _mock_llm_and_money(monkeypatch, llm_reply):
    calls = {"llm": 0}

    def _fake_llm(system, messages):
        calls["llm"] += 1
        return llm_reply

    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _fake_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    return calls


def test_suggest_tasks_confirm_tap_reaches_real_dispatch_e2e(monkeypatch, tmp_path):
    calls = _mock_llm_and_money(
        monkeypatch,
        '[{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"}]',
    )
    monkeypatch.setattr(mod, "_SUGGESTED_TASKS_STATE_PATH", tmp_path / "suggested_tasks_last.json")
    sent, kb_sent, acked = [], [], []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: acked.append(a))

    state = {}
    # Step 1: admin types /suggest_tasks — money-confirm must show, NOT dispatch.
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)
    assert calls["llm"] == 0
    assert len(kb_sent) == 1
    assert "/suggest_tasks" in kb_sent[0][0]
    assert any(btn.get("callback_data") == "confirm:run"
               for row in kb_sent[0][1] for btn in row)
    assert state["pending_confirm"]["cmd"] == "/suggest_tasks"

    # Step 2: admin taps [▶️ Запустить] — a real confirm:run callback_query.
    cq = {
        "id": "cq1", "data": "confirm:run",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, state)

    # The generator must actually run (LLM mock called exactly once) and a
    # tappable suggestion card must reach the chat — NOT "Не знаю такую команду".
    assert calls["llm"] == 1
    assert len(kb_sent) == 2                     # money-confirm card + 1 suggestion card
    text, kb = kb_sent[1]
    assert "не знаю" not in text.lower()
    assert "T1" in text
    assert kb[0][0]["callback_data"].startswith("sugtask:run:")
    assert not sent                               # no plain send() in the happy path
    assert not state.get("pending_confirm")


def test_suggest_tasks_tap_shows_honest_error_when_llm_raises_e2e(monkeypatch):
    """Live path (D2): the confirm:run tap reaches the real dispatch, but the paid
    LLM call raises (Anthropic 400 'credit balance too low', as seen in the prod
    log). guard_spend does NOT wrap do_spend, so the exception used to propagate
    out of the whole callback and the user saw NOTHING (silent), then re-tapped.
    After the fix the user must get an honest '$0 / недоступен' message and the
    handler must NOT raise — NOT silence, NOT 'Не знаю такую команду'."""
    def _raising_llm(system, messages):
        raise RuntimeError("Error code: 400 - Your credit balance is too low")

    # real-guard-like passthrough: do_spend() runs and its exception propagates,
    # exactly as the production guard_spend (spend_guard.py) does.
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _raising_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    state = {"pending_confirm": {"cmd": "/suggest_tasks",
                                 "resume": {"kind": "cmd", "cmd": "/suggest_tasks", "query": ""}}}
    cq = {
        "id": "cq1", "data": "confirm:run",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    # Must NOT raise out of the handler (the whole point of D2).
    mod.handle_callback_query(cq, state)

    assert len(sent) == 1
    assert "не знаю" not in sent[0].lower()
    assert ("$0" in sent[0]) or ("недоступ" in sent[0].lower())


def test_suggest_tasks_stale_confirm_tap_is_not_unknown_command_e2e(monkeypatch):
    """Live path (D1): a SECOND tap on an already-consumed confirm button, i.e.
    pending_confirm is None (the first tap cleared it at _handle_confirm_run:4341
    before the paid call). The old code re-dispatched handle_command with an EMPTY
    cmd → fall-through 'Не знаю такую команду' (line 8072). After the fix the user
    must get a 'кнопка устарела' hint and NOTHING must be dispatched."""
    dispatched = {"n": 0}
    monkeypatch.setattr(mod, "_suggest_tasks_dispatch",
                        lambda cid: dispatched.__setitem__("n", dispatched["n"] + 1))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    state = {"pending_confirm": None}      # already consumed by the prior tap
    cq = {
        "id": "cq2", "data": "confirm:run",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, state)

    assert dispatched["n"] == 0                       # empty resume must NOT dispatch
    assert len(sent) == 1
    assert "не знаю" not in sent[0].lower()
    assert "устарел" in sent[0].lower()


def test_suggest_tasks_direct_call_with_token_bypasses_confirm_e2e(monkeypatch, tmp_path):
    """Direct invocation carrying the one-shot confirmed token (e.g. the same
    re-dispatch _handle_confirm_run performs) must reach the real generator
    without showing a second confirm — no UI round-trip required."""
    calls = _mock_llm_and_money(
        monkeypatch,
        '[{"title": "T2", "signal": "s", "rationale": "r", "draft": "d2", "size": "M"}]',
    )
    monkeypatch.setattr(mod, "_SUGGESTED_TASKS_STATE_PATH", tmp_path / "suggested_tasks_last.json")
    sent, kb_sent = [], []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))

    state = {"_paid_confirmed": "/suggest_tasks"}
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)

    assert len(kb_sent) == 1           # exactly one suggestion card, no confirm prompt
    assert calls["llm"] == 1
    assert not sent
    assert "T2" in kb_sent[0][0]


# ── One-tap run (v0.3): compact card + [🛠 Запустить как dev_task] button ───
def _seed_suggestions_state(monkeypatch, tmp_path, suggestions, gen_id="gen1"):
    from app.services.devtask import suggest as _sug
    from app.services.block_l_common import save_json_safe
    path = tmp_path / "suggested_tasks_last.json"
    monkeypatch.setattr(mod, "_SUGGESTED_TASKS_STATE_PATH", path)
    save_json_safe(path, _sug.build_suggestions_state(suggestions, gen_id, "2026-07-12T08:00:00"))
    return path


def test_sugtask_prefix_is_admin_only_not_friend():
    assert "sugtask:" not in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES


def test_suggest_tasks_multiple_drafts_send_separate_cards_not_one_blob(monkeypatch, tmp_path):
    """(4) several drafts -> separate messages, one per suggestion, so the total
    never has to fit Telegram's 4096-char single-message limit."""
    calls = _mock_llm_and_money(
        monkeypatch,
        '[{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"},'
        '{"title": "T2", "signal": "s", "rationale": "r", "draft": "d2", "size": "M"},'
        '{"title": "T3", "signal": "s", "rationale": "r", "draft": "d3", "size": "L"}]',
    )
    monkeypatch.setattr(mod, "_SUGGESTED_TASKS_STATE_PATH", tmp_path / "suggested_tasks_last.json")
    kb_sent = []
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))

    mod._suggest_tasks_dispatch(ADMIN)

    assert calls["llm"] == 1
    assert len(kb_sent) == 3
    titles = [t for t, kb in kb_sent]
    assert any("T1" in t for t in titles)
    assert any("T2" in t for t in titles)
    assert any("T3" in t for t in titles)
    # each card carries its own distinct index so a tap knows which draft to pull
    datas = [kb[0][0]["callback_data"] for _, kb in kb_sent]
    assert len(set(datas)) == 3
    assert all(d.startswith("sugtask:run:") for d in datas)


def test_suggest_tasks_dispatch_persists_full_drafts_to_state(monkeypatch, tmp_path):
    """(1) the full draft text (not just the compact card) must be recoverable
    from state — cards never carry the draft body themselves."""
    path = tmp_path / "suggested_tasks_last.json"
    monkeypatch.setattr(mod, "_SUGGESTED_TASKS_STATE_PATH", path)
    _mock_llm_and_money(
        monkeypatch,
        '[{"title": "T1", "signal": "s", "rationale": "r", '
        '"draft": "FULL_DRAFT_TEXT_MARKER", "size": "S"}]',
    )
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)

    mod._suggest_tasks_dispatch(ADMIN)

    import json
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["suggestions"][0]["draft"] == "FULL_DRAFT_TEXT_MARKER"
    assert saved["gen_id"]


def test_sugtask_run_tap_dispatches_full_draft_into_standard_devtask_confirm_e2e(monkeypatch, tmp_path):
    """Tapping [🛠 Запустить как dev_task] must land on the SAME confirm gate a
    hand-typed /dev_task gets ([▶️ Запустить]/[Отмена] keyboard) — the button
    only prefills the text, it never bypasses confirmation."""
    from app.services.devtask.queue import DevTaskQueue
    q = DevTaskQueue(base_dir=tmp_path / "queue")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    _seed_suggestions_state(
        monkeypatch, tmp_path,
        [{"title": "T1", "signal": "s", "rationale": "r",
          "draft": "FULL_DRAFT_TEXT_MARKER", "size": "S"}],
        gen_id="gen1",
    )
    kb_sent, acked = [], []
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: acked.append(a))

    cq = {
        "id": "cq1", "data": "sugtask:run:gen1:0",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, {})

    assert len(kb_sent) == 1
    text, kb = kb_sent[0]
    assert "FULL_DRAFT_TEXT_MARKER" in text
    datas = [b["callback_data"] for row in kb for b in row]
    assert any(d.startswith("devtask:confirm:") for d in datas)
    assert any(d.startswith("devtask:cancel:") for d in datas)
    # the queued card really carries the full draft, not a truncated echo
    active = q.list_recent(1)[0]
    assert active["desc"] == "FULL_DRAFT_TEXT_MARKER"


def test_sugtask_run_tap_from_stale_generation_is_rejected(monkeypatch, tmp_path):
    """(5) buttons from a PREVIOUS /suggest_tasks generation must not silently
    fire a stale draft after a fresh /suggest_tasks overwrote the state."""
    from app.services.devtask.queue import DevTaskQueue
    q = DevTaskQueue(base_dir=tmp_path / "queue")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    _seed_suggestions_state(
        monkeypatch, tmp_path,
        [{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"}],
        gen_id="fresh_gen",
    )
    sent, kb_sent, acked = [], [], []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: acked.append(a))

    cq = {
        "id": "cq1", "data": "sugtask:run:stale_gen_from_previous_batch:0",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, {})

    assert kb_sent == []                          # never reached the devtask confirm gate
    assert len(sent) == 1
    assert "устарел" in sent[0].lower()
    assert q.list_recent(5) == []                  # nothing queued


def test_sugtask_run_tap_out_of_range_index_is_rejected(monkeypatch, tmp_path):
    from app.services.devtask.queue import DevTaskQueue
    q = DevTaskQueue(base_dir=tmp_path / "queue")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    _seed_suggestions_state(
        monkeypatch, tmp_path,
        [{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"}],
        gen_id="gen1",
    )
    sent, kb_sent = [], []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    cq = {
        "id": "cq1", "data": "sugtask:run:gen1:7",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, {})

    assert kb_sent == []
    assert len(sent) == 1
    assert "устарел" in sent[0].lower()


def test_sugtask_button_from_previous_generation_is_stale_after_regenerate_e2e(monkeypatch, tmp_path):
    """Literal spec scenario: /suggest_tasks runs TWICE (admin regenerates), then
    a button captured from the FIRST batch is tapped — must be rejected as
    stale, not silently fire the (now superseded) first-batch draft."""
    from app.services.devtask.queue import DevTaskQueue
    q = DevTaskQueue(base_dir=tmp_path / "queue")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "_SUGGESTED_TASKS_STATE_PATH", tmp_path / "suggested_tasks_last.json")
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))

    # Generation 1: capture the button's callback_data.
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", lambda s, m:
                         '[{"title": "OLD", "signal": "s", "rationale": "r", "draft": "OLD_DRAFT", "size": "S"}]')
    mod._suggest_tasks_dispatch(ADMIN)
    old_callback_data = kb_sent[-1][1][0][0]["callback_data"]

    # Generation 2: admin regenerates — overwrites state with a fresh gen_id.
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", lambda s, m:
                         '[{"title": "NEW", "signal": "s", "rationale": "r", "draft": "NEW_DRAFT", "size": "S"}]')
    mod._suggest_tasks_dispatch(ADMIN)

    # Now tap the button captured from generation 1.
    sent2 = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent2.append(t))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    kb_sent.clear()
    cq = {
        "id": "cq1", "data": old_callback_data,
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, {})

    assert kb_sent == []                # never reached the devtask confirm gate
    assert sent2 and "устарел" in sent2[0].lower()
    assert q.list_recent(5) == []       # OLD_DRAFT never queued


def test_sugtask_callback_rejected_for_non_admin(monkeypatch, tmp_path):
    _seed_suggestions_state(
        monkeypatch, tmp_path,
        [{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"}],
        gen_id="gen1",
    )
    acked = []
    monkeypatch.setattr(mod, "answer_callback_query", lambda cid, t="": acked.append(t))
    dispatched = {"n": 0}
    monkeypatch.setattr(mod, "_sugtask_run_dispatch",
                        lambda *a, **k: dispatched.__setitem__("n", dispatched["n"] + 1))
    monkeypatch.setattr(mod, "_is_admin_id", lambda uid: False)

    cq = {
        "id": "cq1", "data": "sugtask:run:gen1:0",
        "message": {"chat": {"id": 999}, "message_id": 55},
        "from": {"id": 999},
    }
    mod.handle_callback_query(cq, {})

    assert dispatched["n"] == 0
    assert acked and "администратор" in acked[0].lower()
