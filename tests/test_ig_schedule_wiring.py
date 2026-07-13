# -*- coding: utf-8 -*-
"""/ig_schedule + /ig_queue bot wiring (control module). $0, mocks only.

``/ig_schedule <аккаунт> <YYYY-MM-DD HH:MM>`` берёт текущую подтверждённую
превью-карточку (``pending_ig_post`` — уже оплаченную/подтверждённую тем же
флоу, что ``/ig_gen``/``/ig_post``) и кладёт её в очередь
(``app.services.ig_schedule``) вместо немедленной публикации. НЕ платно (не в
``PAID`` реестре) — свежая генерация тут не запускается, только постановка в
очередь уже готового поста; если карточки нет, честно просим сначала её
создать (money-safety: не обходим confirm ``/ig_gen`` авто-триггером).
``/ig_queue`` листает pending-очередь с кнопками [Отмена]
(``igsched:cancel:<id>``), тот же паттерн, что ``igpost:``.
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def _cq(data):
    return {
        "id": "cq1", "data": data,
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }


# ---- registry / role --------------------------------------------------------


def test_ig_schedule_is_free_not_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/ig_schedule") is False
    assert _ir.is_paid("/ig_queue") is False


def test_ig_schedule_and_queue_admin_only():
    assert "/ig_schedule" not in mod.FRIEND_ALLOWED_COMMANDS
    assert "/ig_queue" not in mod.FRIEND_ALLOWED_COMMANDS


# ---- /ig_schedule dispatch ---------------------------------------------------


def test_ig_schedule_no_pending_preview_is_honest_no_enqueue(monkeypatch):
    from app.services import ig_schedule as igsc
    calls = {"enqueue": 0}
    monkeypatch.setattr(igsc, "enqueue", lambda **k: calls.__setitem__("enqueue", 1) or {})
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    state = {}
    mod.handle_command(ADMIN, "/ig_schedule", "vera_ai_ua 2026-07-14 09:00", state)

    assert calls["enqueue"] == 0
    assert "превью" in sent["t"].lower() or "ig_gen" in sent["t"].lower()


def test_ig_schedule_missing_args_shows_usage(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    state = {}
    mod.handle_command(ADMIN, "/ig_schedule", "", state)
    assert "использ" in sent["t"].lower() or "/ig_schedule" in sent["t"]


def test_ig_schedule_bad_datetime_keeps_pending_and_is_honest(monkeypatch):
    from datetime import datetime

    from app.services import ig_post as igp
    monkeypatch.setattr(mod, "_ig_schedule_now", lambda: datetime(2026, 7, 13, 12, 0))
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    save_calls = {"n": 0}
    monkeypatch.setattr(mod, "save_state", lambda s: save_calls.__setitem__("n", save_calls["n"] + 1))

    state = {igp.IG_POST_PENDING_KEY: {"photo_url": "https://pub/x.jpg", "caption": "c",
                                        "topic": "кава", "source": "C:/tmp/x.jpg"}}
    mod.handle_command(ADMIN, "/ig_schedule", "vera_ai_ua завтра утром", state)

    assert igp.IG_POST_PENDING_KEY in state  # not consumed on error
    assert sent["t"]
    assert save_calls["n"] == 0


def test_ig_schedule_past_datetime_rejected(monkeypatch):
    from datetime import datetime

    from app.services import ig_post as igp
    monkeypatch.setattr(mod, "_ig_schedule_now", lambda: datetime(2026, 7, 13, 12, 0))
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    state = {igp.IG_POST_PENDING_KEY: {"photo_url": "https://pub/x.jpg", "caption": "c",
                                        "topic": "кава", "source": "C:/tmp/x.jpg"}}
    mod.handle_command(ADMIN, "/ig_schedule", "vera_ai_ua 2026-07-10 09:00", state)

    assert igp.IG_POST_PENDING_KEY in state
    assert "будущ" in sent["t"].lower()


def test_ig_schedule_happy_path_enqueues_and_clears_pending(monkeypatch, tmp_path):
    from datetime import datetime

    from app.services import ig_post as igp
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    monkeypatch.setattr(mod, "_ig_schedule_now", lambda: datetime(2026, 7, 13, 12, 0))
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    save_calls = {"n": 0}
    monkeypatch.setattr(mod, "save_state", lambda s: save_calls.__setitem__("n", save_calls["n"] + 1))

    state = {igp.IG_POST_PENDING_KEY: {"photo_url": "https://pub/x.jpg", "caption": "Смачна кава",
                                        "topic": "кава", "source": "C:/tmp/x.jpg"}}
    mod.handle_command(ADMIN, "/ig_schedule", "vera_ai_ua 2026-07-14 09:00", state)

    assert igp.IG_POST_PENDING_KEY not in state
    assert save_calls["n"] == 1
    assert "2026-07-14" in sent["t"]
    assert "vera_ai_ua" in sent["t"]

    from app.services import ig_schedule as igsc
    queued = igsc.list_posts(status="pending")
    assert len(queued) == 1
    assert queued[0]["photo_url"] == "https://pub/x.jpg"
    assert queued[0]["account_key"] == "vera_ai_ua"
    assert queued[0]["chat_id"] == ADMIN


# ---- /ig_queue dispatch -------------------------------------------------------


def test_ig_queue_empty_sends_plain_message(monkeypatch, tmp_path):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: kb_sent.append(1))

    mod.handle_command(ADMIN, "/ig_queue", "", {})

    assert "пуста" in sent["t"].lower()
    assert not kb_sent


def test_ig_queue_lists_pending_with_cancel_buttons(monkeypatch, tmp_path):
    from datetime import datetime

    from app.services import ig_schedule as igsc
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igsc.enqueue(chat_id=ADMIN, account_key="vera_ai_ua",
                           run_at=datetime(2026, 7, 14, 9, 0),
                           pending={"photo_url": "u", "caption": "c", "topic": "кава", "source": "s"},
                           now=now)
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    mod.handle_command(ADMIN, "/ig_queue", "", {})

    assert len(kb_sent) == 1
    text, kb = kb_sent[0]
    assert "vera_ai_ua" in text
    cbs = [btn.get("callback_data") for row in kb for btn in row]
    assert "igsched:cancel:%s" % record["id"] in cbs


# ---- igsched: callback -------------------------------------------------------


def test_igsched_cancel_callback_removes_post(monkeypatch, tmp_path):
    from datetime import datetime

    from app.services import ig_schedule as igsc
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    now = datetime(2026, 7, 13, 12, 0)
    record = igsc.enqueue(chat_id=ADMIN, account_key="a", run_at=datetime(2026, 7, 14, 9, 0),
                           pending={"photo_url": "u", "caption": "c", "topic": "t", "source": "s"},
                           now=now)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    edits = []
    monkeypatch.setattr(mod, "edit_message_with_keyboard",
                        lambda cid, mid, t, kb, *a, **k: edits.append(t))

    mod.handle_callback_query(_cq("igsched:cancel:%s" % record["id"]), {})

    assert igsc.get_post(record["id"])["status"] == "cancelled"
    assert edits and "отмен" in edits[0].lower()


def test_igsched_cancel_callback_unknown_id_is_honest(monkeypatch, tmp_path):
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    monkeypatch.setattr(mod, "edit_message_with_keyboard", lambda *a, **k: None)
    answered = []
    monkeypatch.setattr(mod, "answer_callback_query", lambda cq_id, text="": answered.append(text))

    mod.handle_callback_query(_cq("igsched:cancel:sched_doesnotexist"), {})

    assert answered and ("не в очеред" in answered[0].lower() or "устарел" in answered[0].lower())
