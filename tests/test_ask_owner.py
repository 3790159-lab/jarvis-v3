# -*- coding: utf-8 -*-
"""`ask_owner`: спросить владельца кнопками и ДОЖДАТЬСЯ ответа.

Зачем отдельный бот со СВОИМ токеном: Telegram отдаёт long-poll ровно одному
потребителю на токен. Второй `getUpdates` на токене контрол-бота дал бы 409 и
уронил ЖИВОЙ пульт владельца — тот самый инвариант, который уже выписан в
дрил-харнессе. Свой токен = своя очередь апдейтов, конфликта нет по
конструкции.

Сторожа написаны ОТ КОНТРАКТА, а не от реализации. Контракт короткий:
  1. молчание — это НЕТ, и оно ГРОМКОЕ (журнал, а не тишина);
  2. решает только владелец и только по ТОМУ вопросу, который задан;
  3. в журнале оба конца — «спросил» и «получил»; строка без пары это улика,
     а не пробел в данных.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "ask_owner", REPO_ROOT / "scripts" / "ask_owner.py")
ao = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ao)

OWNER = "237616472"
OPTS = ["Да", "Нет"]


def press(qid, idx, *, chat=OWNER, uid=1):
    """Апдейт-нажатие кнопки."""
    return {"update_id": uid,
            "callback_query": {"id": "cb1",
                               "from": {"id": int(chat)},
                               "data": f"ask:{qid}:{idx}"}}


# ── 1. решает владелец и только по ЗАДАННОМУ вопросу ──────────────────────

def test_owner_press_returns_the_exact_option_text():
    """Возвращается ТЕКСТ варианта, а не индекс: вызывающий сравнивает со
    своим списком, и рассинхрон нумерации не должен молча менять смысл."""
    assert ao.parse_decision([press("q1", 0)], qid="q1",
                             owner_chat_id=OWNER, options=OPTS) == "Да"
    assert ao.parse_decision([press("q1", 1)], qid="q1",
                             owner_chat_id=OWNER, options=OPTS) == "Нет"


def test_press_from_a_foreign_chat_is_ignored():
    """Кнопку видит только владелец, но пересланное сообщение или чужой
    аккаунт не имеют права решать за него."""
    assert ao.parse_decision([press("q1", 0, chat="999")], qid="q1",
                             owner_chat_id=OWNER, options=OPTS) is None


def test_press_for_a_DIFFERENT_question_is_ignored():
    """Устаревшая кнопка от прошлого вопроса не отвечает на текущий. Без
    этого «Да», нажатое полчаса назад на другой вопрос, разрешило бы то, о
    чём владельца не спрашивали."""
    assert ao.parse_decision([press("q_old", 0)], qid="q_new",
                             owner_chat_id=OWNER, options=OPTS) is None


def test_index_out_of_range_is_ignored_not_clamped():
    """Клампить нельзя: «вариант 5» из трёх не означает «последний»."""
    assert ao.parse_decision([press("q1", 7)], qid="q1",
                             owner_chat_id=OWNER, options=OPTS) is None


def test_malformed_callback_data_is_ignored():
    for bad in ("", "ask:q1", "ask:q1:x", "нечто", "ask::0"):
        upd = [{"update_id": 1, "callback_query": {
            "id": "c", "from": {"id": int(OWNER)}, "data": bad}}]
        assert ao.parse_decision(upd, qid="q1", owner_chat_id=OWNER,
                                 options=OPTS) is None, bad


def test_non_callback_updates_do_not_decide():
    """Обычное сообщение в чат — не ответ. Иначе владелец, написавший «да»
    текстом кому-то другому, случайно разрешил бы действие."""
    upd = [{"update_id": 1, "message": {"chat": {"id": int(OWNER)},
                                        "text": "Да"}}]
    assert ao.parse_decision(upd, qid="q1", owner_chat_id=OWNER,
                             options=OPTS) is None


def test_last_valid_press_wins_when_owner_changed_his_mind():
    upds = [press("q1", 0, uid=1), press("q1", 1, uid=2)]
    assert ao.parse_decision(upds, qid="q1", owner_chat_id=OWNER,
                             options=OPTS) == "Нет"


# ── 2. текст вопроса называет СУТЬ ────────────────────────────────────────

def test_text_contains_the_command_itself():
    """Т2 владельца: по «tool call» решение принять нельзя."""
    text = ao.build_text(question="Мержу ветку в транк?",
                         context="git merge --ff-only feat/ask-owner",
                         deadline_ts=1786900000)
    assert "git merge --ff-only feat/ask-owner" in text
    assert "Мержу ветку в транк?" in text


def test_text_states_the_deadline_and_the_default():
    """Без срока и без слова «иначе НЕТ» отказ по таймауту выглядит как
    поломка моста, а не как решение."""
    text = ao.build_text(question="q", context=None, deadline_ts=1786900000)
    assert "НЕТ" in text.upper()


def test_long_command_is_truncated_visibly():
    text = ao.build_text(question="q", context="x" * 5000,
                         deadline_ts=1786900000)
    assert len(text) < 4096, "Telegram режет сообщение >4096 — режем сами"
    assert "…" in text, "усечение обязано быть ВИДНЫМ"


# ── 3. клавиатура ─────────────────────────────────────────────────────────

def test_keyboard_carries_question_id_in_every_button():
    kb = ao.build_keyboard("q1", OPTS)
    datas = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert datas == ["ask:q1:0", "ask:q1:1"]


def test_keyboard_button_labels_are_the_options_verbatim():
    kb = ao.build_keyboard("q1", ["Влить", "Отложить"])
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert labels == ["Влить", "Отложить"]


def test_empty_options_is_a_loud_refusal():
    """Пустой список = вопрос без ответов. Молчаливый дефолт — класс бага."""
    with pytest.raises(ao.AskError):
        ao.build_keyboard("q1", [])


# ── 4. смещение getUpdates ────────────────────────────────────────────────

def test_offset_advances_past_the_highest_seen_update():
    assert ao.next_offset([{"update_id": 5}, {"update_id": 9}], 0) == 10


def test_offset_never_goes_backwards():
    """Иначе один и тот же апдейт читается вечно."""
    assert ao.next_offset([{"update_id": 3}], 100) == 100


def test_offset_survives_empty_poll():
    assert ao.next_offset([], 42) == 42


# ── 5. журнал: ОБА конца ──────────────────────────────────────────────────

def test_journal_records_the_question_and_the_outcome(tmp_path):
    j = tmp_path / "ask_owner.jsonl"
    ao.journal(j, "asked", qid="q1", question="Мержу?",
               context="git merge x", options=OPTS)
    ao.journal(j, "decided", qid="q1", decision="Нет", by="telegram")
    lines = [json.loads(x) for x in j.read_text("utf-8").splitlines()]
    assert [l["event"] for l in lines] == ["asked", "decided"]
    assert lines[0]["context"] == "git merge x"
    assert lines[1]["decision"] == "Нет"
    assert all(l["qid"] == "q1" for l in lines), "без qid строки не сшить"
    assert all("ts" in l for l in lines)


def test_timeout_is_journaled_as_a_decision_not_as_silence(tmp_path):
    """Требование владельца дословно: «не ответил — не делаем, а не делаем
    молча». Строка о таймауте обязана быть, и в ней обязано стоять, что это
    ОТКАЗ, а не отсутствие данных."""
    j = tmp_path / "ask_owner.jsonl"
    ao.journal(j, "decided", qid="q1", decision=ao.TIMEOUT_DECISION,
               by="timeout")
    line = json.loads(j.read_text("utf-8").splitlines()[-1])
    assert line["by"] == "timeout"
    assert line["decision"] == ao.TIMEOUT_DECISION


def test_journal_is_append_only(tmp_path):
    j = tmp_path / "ask_owner.jsonl"
    for i in range(3):
        ao.journal(j, "asked", qid=f"q{i}")
    assert len(j.read_text("utf-8").splitlines()) == 3


# ── 6. идентификатор вопроса ──────────────────────────────────────────────

def test_question_ids_differ_for_different_questions():
    a = ao.make_question_id("Мержу A?", now=1000.0)
    b = ao.make_question_id("Мержу B?", now=1000.0)
    assert a != b


def test_question_ids_differ_for_the_same_question_asked_twice():
    """Иначе нажатие по ПРОШЛОМУ такому же вопросу закроет новый."""
    a = ao.make_question_id("Мержу?", now=1000.0)
    b = ao.make_question_id("Мержу?", now=2000.0)
    assert a != b


def test_question_id_fits_telegram_callback_data_limit():
    qid = ao.make_question_id("x" * 500, now=1000.0)
    assert len(f"ask:{qid}:0".encode()) <= 64, "callback_data >64 байт Telegram отвергает"
