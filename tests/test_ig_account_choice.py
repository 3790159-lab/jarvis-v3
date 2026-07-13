# -*- coding: utf-8 -*-
"""Account-choice UX for /ig_stats, /ig_post, /ig_gen (bot wiring). $0, mocks only.

2+ IG-аккаунта в сторе и команда без явного ``@<account_key>`` -> inline-кнопки
[jtest_lab_] [vera_ai_ua] вместо молчаливого дефолта (риск запостить/посмотреть
не туда — квоты и лента). 1 аккаунт в сторе -> как раньше, без вопроса. Выбор
запоминается на чат на TTL 1ч (``app.services.ig_accounts`` remember/get/forget).
"""
import importlib

from app.services import ig_accounts as iga

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"
PENDING_KEY = "pending_ig_account_choice"


def _two_accounts():
    iga.save_account("jtest_lab_", access_token="A")
    iga.save_account("vera_ai_ua", access_token="B")


def _cq(data, uid=ADMIN):
    return {
        "id": "cq1", "data": data,
        "message": {"chat": {"id": int(uid)}, "message_id": 55},
        "from": {"id": int(uid)},
    }


def setup_function(_fn):
    # In-process TTL cache is NOT isolated by the tmp-file account-store
    # fixture (it's keyed only by chat_id) — forget any leftover choice from
    # a previous test before each test, same convention as mod._LAST_IG_MEDIA.
    iga.forget_account_choice(ADMIN)
    iga.forget_account_choice("999")


# ---- 0/1 account: no prompt, pass straight through -------------------------


def test_zero_accounts_no_prompt(monkeypatch):
    fired = {}
    monkeypatch.setattr(mod, "_ig_stats_dispatch",
                        lambda cid, query="": fired.update(cid=cid, query=query))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))
    mod.handle_command(ADMIN, "/ig_stats", "", {})
    assert fired == {"cid": ADMIN, "query": ""}
    assert not kb


def test_one_account_no_prompt(monkeypatch):
    iga.save_account("jtest_lab_", access_token="A")
    fired = {}
    monkeypatch.setattr(mod, "_ig_stats_dispatch",
                        lambda cid, query="": fired.update(cid=cid, query=query))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))
    mod.handle_command(ADMIN, "/ig_stats", "", {})
    assert fired == {"cid": ADMIN, "query": ""}
    assert not kb


# ---- 2+ accounts, no explicit @account -> prompt, dispatch NOT called ------


def test_two_accounts_no_explicit_arg_prompts_and_blocks_dispatch(monkeypatch):
    _two_accounts()
    fired = {"count": 0}
    monkeypatch.setattr(mod, "_ig_stats_dispatch",
                        lambda cid, query="": fired.__setitem__("count", fired["count"] + 1))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, text, rows: kb.append((cid, text, rows)))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = {}
    mod.handle_command(ADMIN, "/ig_stats", "", state)

    assert fired["count"] == 0
    assert len(kb) == 1
    cid, text, rows = kb[0]
    assert cid == ADMIN
    buttons = [btn["text"] for row in rows for btn in row]
    assert buttons == ["jtest_lab_", "vera_ai_ua"]
    assert state[PENDING_KEY] == {"cmd": "/ig_stats", "query": ""}


def test_two_accounts_ig_post_no_explicit_arg_prompts(monkeypatch):
    _two_accounts()
    fired = {"count": 0}
    monkeypatch.setattr(mod, "_ig_post_dispatch",
                        lambda cid, query, state: fired.__setitem__("count", fired["count"] + 1))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, text, rows: kb.append(rows))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = {}
    mod.handle_command(ADMIN, "/ig_post", "pic.jpg тема", state)

    assert fired["count"] == 0
    assert len(kb) == 1
    assert state[PENDING_KEY] == {"cmd": "/ig_post", "query": "pic.jpg тема"}


def test_two_accounts_ig_gen_no_explicit_arg_prompts_before_money_confirm(monkeypatch):
    """/ig_gen is PAID (blanket money-confirm) — the account prompt must fire
    FIRST so the two questions never both show for one command invocation."""
    _two_accounts()
    dispatch_fired = {"count": 0}
    monkeypatch.setattr(mod, "_ig_gen_dispatch",
                        lambda cid, query, state: dispatch_fired.__setitem__(
                            "count", dispatch_fired["count"] + 1))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, text, rows: kb.append((text, rows)))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = {}
    mod.handle_command(ADMIN, "/ig_gen", "тема поста", state)

    assert dispatch_fired["count"] == 0
    assert len(kb) == 1                      # account prompt only, no money-confirm card yet
    assert state[PENDING_KEY] == {"cmd": "/ig_gen", "query": "тема поста"}
    assert "pending_confirm" not in state or not state.get("pending_confirm")


# ---- explicit @account with 2+ accounts -> no prompt -----------------------


def test_two_accounts_explicit_account_arg_skips_prompt(monkeypatch):
    _two_accounts()
    fired = {}
    monkeypatch.setattr(mod, "_ig_stats_dispatch",
                        lambda cid, query="": fired.update(cid=cid, query=query))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))
    mod.handle_command(ADMIN, "/ig_stats", "@vera_ai_ua", {})
    assert fired == {"cid": ADMIN, "query": "@vera_ai_ua"}
    assert not kb


def test_two_accounts_ig_gen_client_brand_resolves_account_skips_prompt(monkeypatch):
    """client=<name> whose brand.md maps to an account_key (dir-name
    convention, real fixture clients/vera_ai_ua/brand.md — same one
    test_ig_gen_wiring.py's brand-mapping tests use) resolves the ambiguity
    by itself — no prompt."""
    _two_accounts()

    fired = {"count": 0}
    monkeypatch.setattr(mod, "_ig_gen_dispatch",
                        lambda cid, query, state: fired.__setitem__("count", fired["count"] + 1))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, text, rows: kb.append(text))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    state = {}
    mod.handle_command(ADMIN, "/ig_gen", "client=vera_ai_ua тема", state)

    # brand resolved the account by itself -> no account-choice card, only
    # /ig_gen's own (unrelated) money-confirm card fires; dispatch waits for
    # that confirm tap, same as the existing non-ambiguous /ig_gen flow.
    assert not any("аккаунт" in t for t in kb)
    assert state.get(PENDING_KEY) is None
    assert fired["count"] == 0


# ---- igacct: callback resumes the original command -------------------------


def test_igacct_pick_resumes_original_command_with_account_prefix(monkeypatch):
    resumed = {}
    monkeypatch.setattr(mod, "handle_command",
                        lambda cid, cmd, query, state: resumed.update(
                            cid=cid, cmd=cmd, query=query))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    state = {PENDING_KEY: {"cmd": "/ig_gen", "query": "тема поста"}}
    mod.handle_callback_query(_cq("igacct:pick:vera_ai_ua"), state)

    assert resumed == {"cid": ADMIN, "cmd": "/ig_gen", "query": "@vera_ai_ua тема поста"}
    assert state[PENDING_KEY] is None


def test_igacct_pick_remembers_choice_for_the_chat(monkeypatch):
    monkeypatch.setattr(mod, "handle_command", lambda *a, **k: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    state = {PENDING_KEY: {"cmd": "/ig_stats", "query": ""}}
    mod.handle_callback_query(_cq("igacct:pick:jtest_lab_"), state)

    assert iga.get_remembered_account(ADMIN) == "jtest_lab_"


def test_igacct_pick_stale_pending_is_honest(monkeypatch):
    answered = []
    monkeypatch.setattr(mod, "answer_callback_query",
                        lambda cq_id, text="": answered.append(text))
    resumed = {"count": 0}
    monkeypatch.setattr(mod, "handle_command",
                        lambda *a, **k: resumed.__setitem__("count", resumed["count"] + 1))

    state = {}  # no pending_ig_account_choice
    mod.handle_callback_query(_cq("igacct:pick:vera_ai_ua"), state)

    assert resumed["count"] == 0
    assert answered and "устар" in answered[0].lower()


def test_igacct_callback_blocked_for_non_admin(monkeypatch):
    answered = []
    monkeypatch.setattr(mod, "answer_callback_query",
                        lambda cq_id, text="": answered.append(text))
    resumed = {"count": 0}
    monkeypatch.setattr(mod, "handle_command",
                        lambda *a, **k: resumed.__setitem__("count", resumed["count"] + 1))
    monkeypatch.setattr(mod, "_is_admin_id", lambda uid: False)
    monkeypatch.setattr(mod, "ALLOWED_CHAT_ID", "999999999")

    state = {PENDING_KEY: {"cmd": "/ig_stats", "query": ""}}
    mod.handle_callback_query(_cq("igacct:pick:vera_ai_ua", uid="111"), state)

    assert resumed["count"] == 0
    assert answered and "администратора" in answered[0]


# ---- TTL: remembered choice skips the prompt on a later command -----------


def test_remembered_choice_skips_prompt_on_later_command(monkeypatch):
    _two_accounts()
    iga.remember_account_choice(ADMIN, "vera_ai_ua")
    fired = {}
    monkeypatch.setattr(mod, "_ig_stats_dispatch",
                        lambda cid, query="": fired.update(cid=cid, query=query))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))
    mod.handle_command(ADMIN, "/ig_stats", "", {})

    assert not kb
    assert fired == {"cid": ADMIN, "query": "@vera_ai_ua"}


def test_paid_confirmed_token_survives_account_detour_without_double_confirm(monkeypatch):
    """A money-confirm already approved for THIS /ig_gen call (e.g. resumed
    from run_intent's NL-routed confirm tap) must carry through the account
    picker so the user isn't asked to pay-confirm twice for one invocation."""
    _two_accounts()
    dispatch_fired = {}
    monkeypatch.setattr(mod, "_ig_gen_dispatch",
                        lambda cid, query, state: dispatch_fired.update(query=query))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, text, rows: kb.append(text))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    state = {"_paid_confirmed": "/ig_gen"}
    mod.handle_command(ADMIN, "/ig_gen", "тема", state)

    # account picker shown, dispatch not yet fired, token stashed (not left
    # dangling at top level so an unrelated later /ig_gen wouldn't reuse it)
    assert not dispatch_fired
    assert "_paid_confirmed" not in state
    assert state[PENDING_KEY]["paid_confirmed"] == "/ig_gen"
    kb.clear()

    mod.handle_callback_query(_cq("igacct:pick:vera_ai_ua"), state)

    # resumed straight into dispatch — no second money-confirm card shown
    assert dispatch_fired == {"query": "@vera_ai_ua тема"}
    assert not kb


def test_paid_confirmed_token_does_not_leak_to_unrelated_later_call(monkeypatch):
    """If the account picker is abandoned, a stale top-level _paid_confirmed
    must not silently authorise a later, separate /ig_gen invocation."""
    _two_accounts()
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    state = {"_paid_confirmed": "/ig_gen"}
    mod.handle_command(ADMIN, "/ig_gen", "тема1", state)  # stashes+pops the token
    assert "_paid_confirmed" not in state

    # user abandons the picker, later sends a fresh explicit-account /ig_gen
    confirm_shown = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, text, rows: confirm_shown.append(text))
    dispatch_fired = {"count": 0}
    monkeypatch.setattr(mod, "_ig_gen_dispatch",
                        lambda cid, query, state: dispatch_fired.__setitem__(
                            "count", dispatch_fired["count"] + 1))
    mod.handle_command(ADMIN, "/ig_gen", "@jtest_lab_ тема2", state)

    assert dispatch_fired["count"] == 0     # must NOT skip the money-confirm
    assert len(confirm_shown) == 1


def test_remembered_choice_expired_prompts_again(monkeypatch):
    _two_accounts()
    fake_now = [1_000_000.0]
    monkeypatch.setattr(iga.time, "time", lambda: fake_now[0])
    iga.remember_account_choice(ADMIN, "vera_ai_ua")
    fake_now[0] += iga.ACCOUNT_CHOICE_TTL_S + 1

    fired = {"count": 0}
    monkeypatch.setattr(mod, "_ig_stats_dispatch",
                        lambda cid, query="": fired.__setitem__("count", fired["count"] + 1))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb.append(1))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    mod.handle_command(ADMIN, "/ig_stats", "", {})

    assert fired["count"] == 0
    assert len(kb) == 1
