# -*- coding: utf-8 -*-
"""Замер радиуса переответа: сторожа на решения, а не на текст вывода.

Класс ошибки, ради которого замер существует (живой случай 17.08): рестарт
раннера заставил catch-up ответить на сообщение 13.8-часовой давности в
ЭСКАЛИРОВАННОМ диалоге. Бот молчал не потому, что не успел, а потому что
передал ведение человеку — и ответил поверх него.

Сторожа проверяют четыре решения замера:
  1. последнее слово за клиентом = диалог будет переотвечен;
  2. последнее слово за ботом = не будет (иначе тревога станет фоном);
  3. порог возраста тот же, что у самого catch-up (два числа на одну вещь
     однажды разъедутся);
  4. «не смогли посмотреть» ОТЛИЧАЕТСЯ от «посмотрели, чисто».
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import chatter_catchup_radius as radius  # noqa: E402
from chatter.telethon_run import CATCHUP_MAX_AGE_SECONDS  # noqa: E402

NOW = 1_700_000_000.0


def make_db(path: Path, rows, contacts=None) -> Path:
    """rows: (contact_id, role, ts_offset_seconds, text) — offset ОТ NOW назад."""
    con = sqlite3.connect(path)
    con.execute("create table messages (id integer primary key, contact_id text, "
                "role text, text text, ts real)")
    con.execute("create table contacts (contact_id text primary key, state text, "
                "paused integer, human_took_over integer)")
    for contact_id, role, back, text in rows:
        con.execute("insert into messages (contact_id, role, text, ts) values (?,?,?,?)",
                    (contact_id, role, text, NOW - back))
    for contact_id, state, paused, took in (contacts or []):
        con.execute("insert into contacts (contact_id, state, paused, human_took_over) "
                    "values (?,?,?,?)", (contact_id, state, paused, took))
    con.commit()
    con.close()
    return path


def test_a_dialog_whose_last_word_is_the_clients_is_named(tmp_path):
    """Ловит: рестарт вслепую. Один диалог, последнее слово клиента — его и
    переответят, и человек обязан узнать об этом ДО подъёма, а не из истории."""
    db = make_db(tmp_path / "c.db", [
        ("111:yarina", "assistant", 7200, "Зв'яжу вас зі старшим майстром"),
        ("111:yarina", "user", 3600, "Покажіть договір, будь ласка"),
    ], contacts=[("111:yarina", "escalated", 0, 0)])

    rows = radius.dialogs_at_risk(db, now=NOW)

    assert [r["contact_id"] for r in rows] == ["111:yarina"], rows
    assert rows[0]["deliberate_silence"] is True, (
        "эскалированный диалог не помечен как молчание ПО РЕШЕНИЮ — а именно "
        "он и стоит дороже всего: ответ идёт поверх человека")
    assert "договір" in rows[0]["text"]


def test_a_dialog_the_bot_already_answered_is_not_a_risk(tmp_path):
    """Парная: тревога, которая звучит всегда, — это фон, а не сторож.

    Без этой стороны замер мог бы честно возвращать ВСЕ диалоги и выглядеть
    работающим ровно до первого рестарта, который из-за него не сделали.
    """
    db = make_db(tmp_path / "c.db", [
        ("222:volska", "user", 5400, "Скільки коштує полірування?"),
        ("222:volska", "assistant", 5000, "5 000–8 000 грн"),
    ], contacts=[("222:volska", "active", 0, 0)])

    assert radius.dialogs_at_risk(db, now=NOW) == []


def test_a_message_older_than_the_catchup_cap_is_not_a_risk(tmp_path):
    """Ловит: тревогу по диалогам, которые catch-up и так не тронет.

    У catch-up есть возрастной порог; всё старше он пропускает сам. Замер
    обязан брать порог ИЗ НЕГО — иначе два числа на одну вещь разъедутся, и
    меньшее погасит большее молча.
    """
    db = make_db(tmp_path / "c.db", [
        ("333:yarina", "user", CATCHUP_MAX_AGE_SECONDS + 60, "давнє питання"),
    ], contacts=[("333:yarina", "escalated", 0, 0)])
    assert radius.dialogs_at_risk(db, now=NOW) == []

    fresh = make_db(tmp_path / "d.db", [
        ("333:yarina", "user", CATCHUP_MAX_AGE_SECONDS - 60, "свіже питання"),
    ], contacts=[("333:yarina", "escalated", 0, 0)])
    assert len(radius.dialogs_at_risk(fresh, now=NOW)) == 1


def test_the_cap_is_taken_from_catchup_itself_not_copied():
    """Ловит: вторую правду об одном числе.

    Значение по умолчанию обязано БЫТЬ тем же объектом-числом, что у catch-up.
    Скопированная константа живёт своей жизнью ровно до первой правки порога.
    """
    import inspect

    default = inspect.signature(radius.dialogs_at_risk).parameters["max_age_seconds"].default
    assert default == CATCHUP_MAX_AGE_SECONDS
    src = (REPO_ROOT / "scripts" / "chatter_catchup_radius.py").read_text(encoding="utf-8")
    assert "from chatter.telethon_run import CATCHUP_MAX_AGE_SECONDS" in src, (
        "порог не импортирован, а переписан — он разъедется с catch-up молча")


@pytest.mark.parametrize("kind", ["нет файла", "не БД"])
def test_a_measurement_that_did_not_happen_is_not_a_clean_one(tmp_path, kind):
    """Ловит: молчание инструмента, прочитанное как «чисто».

    Самая дорогая ошибка такого замера — вернуть пустой список, когда
    посмотреть НЕ УДАЛОСЬ. Человек прочитает это как разрешение и перезапустит
    раннера поверх живого диалога.
    """
    if kind == "нет файла":
        target = tmp_path / "нет.db"
    else:
        target = tmp_path / "мусор.db"
        target.write_bytes(b"\x00\x01 not a database at all")

    with pytest.raises(radius.RadiusError):
        radius.dialogs_at_risk(target, now=NOW)


def test_paused_and_human_took_over_count_as_deliberate_silence(tmp_path):
    """Ловит: узкое понимание «бот молчит намеренно».

    Эскалация — не единственный такой случай: пауза и взятый человеком диалог
    означают ровно то же. Список состояний может расти, но каждое новое
    обязано попадать сюда вместе с кодом.
    """
    db = make_db(tmp_path / "c.db", [
        ("444:yarina", "user", 600, "Ви ще тут?"),
        ("555:yarina", "user", 600, "Чекаю відповіді"),
    ], contacts=[("444:yarina", "active", 1, 0), ("555:yarina", "active", 0, 1)])

    rows = {r["contact_id"]: r for r in radius.dialogs_at_risk(db, now=NOW)}

    assert rows["444:yarina"]["deliberate_silence"] is True, "пауза не учтена"
    assert rows["555:yarina"]["deliberate_silence"] is True, "human_took_over не учтён"


def test_the_riskiest_dialogs_are_printed_first(tmp_path):
    """Ловит: список, в котором главное потерялось.

    Человек читает первые строки. Диалог, где ответ пойдёт поверх человека,
    обязан стоять выше того, где клиент просто ждёт бота.
    """
    db = make_db(tmp_path / "c.db", [
        ("666:yarina", "user", 300, "просто чекаю"),
        ("777:yarina", "user", 7200, "передали старшому майстру?"),
    ], contacts=[("666:yarina", "active", 0, 0), ("777:yarina", "escalated", 0, 0)])

    rows = radius.dialogs_at_risk(db, now=NOW)
    assert rows[0]["contact_id"] == "777:yarina", [r["contact_id"] for r in rows]
