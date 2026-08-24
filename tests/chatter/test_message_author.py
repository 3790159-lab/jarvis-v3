"""TG-1: слова владельца в истории отличимы от слов бота.

Повод — замер на боевой базе 25.08: ручное сообщение владельца ложится в
историю как `role='assistant'`, то есть РОВНО так же, как реплика бота. Пока
перехват редок и виден по карточке, это терпимо; как только отправка станет
кнопкой в панели, неразличимость означает, что владелец не видит в ленте
собственных слов, а разбор жалобы «кто это написал» упирается в догадку.

Лечение — АВТОР ОТДЕЛЬНЫМ ПОЛЕМ, а не ролью. Роль обязана остаться прежней:
`brain.build_messages` мапит её напрямую в поле `role` вызова Anthropic, и
роли 'human' там нет. Именно это и стережёт последний тест файла — он про
границу, которую правка не должна была сдвинуть.

⚠️ Сторожа писал автор правки (отступление от
[[jarvis-guards-not-by-the-plan-author]]): правка мелкая и срочная. При
разборе стоит перечитать их отдельно.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from chatter.core.brain import build_messages
from chatter.storage.db import Store

_RUNNER = Path(__file__).resolve().parents[2] / "chatter" / "telethon_run.py"


def _store():
    return Store(":memory:")


def test_umolchanie_avtora_bot():
    s = _store()
    s.add_message("c1", "assistant", "реплика бота", ts=1.0)
    assert s.history("c1")[0]["author"] == "bot"


def test_ruchnoe_soobshenie_vladelca_pomecheno_human():
    s = _store()
    s.add_message("c1", "assistant", "я сам отвечу", ts=1.0, author="human")
    row = s.history("c1")[0]
    assert row["author"] == "human"
    # Роль НЕ меняется: для лида это та же персона, а для API — assistant.
    assert row["role"] == "assistant"


def test_istoria_razlichaet_dvuh_avtorov_v_odnoi_lente():
    s = _store()
    s.add_message("c1", "user", "здравствуйте", ts=1.0)
    s.add_message("c1", "assistant", "добрый день", ts=2.0)
    s.add_message("c1", "assistant", "это уже я, владелец", ts=3.0, author="human")
    assert [(m["role"], m["author"]) for m in s.history("c1")] == [
        ("user", "bot"), ("assistant", "bot"), ("assistant", "human")]


def test_staraya_baza_migriruet_i_starye_stroki_ostayutsya_NEIZVESTNYMI(tmp_path):
    """Миграция добавляет колонку и НЕ придумывает авторство задним числом.

    Пометить исторические строки 'bot' значило бы соврать ровно про те
    сообщения, ради которых колонка заводится: ручные реплики владельца,
    которые до сих пор были неотличимы. NULL читается «не знаем»."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " contact_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,"
        " ts REAL NOT NULL);"
        "INSERT INTO messages(contact_id, role, text, ts)"
        " VALUES ('c1','assistant','старая реплика',1.0);")
    con.commit()
    con.close()

    store = Store(str(path))
    cols = {r[1] for r in store._conn.execute("PRAGMA table_info(messages)")}
    assert "author" in cols

    old = store.history("c1")[0]
    assert old["author"] is None, "историческим строкам авторство не выдумываем"

    store.add_message("c1", "assistant", "новая реплика", ts=2.0, author="human")
    assert [m["author"] for m in store.history("c1")] == [None, "human"]


def test_migracia_idempotentna_povtornoe_otkrytie_ne_padaet(tmp_path):
    """Гардиан перезапускает раннер постоянно: миграция, падающая на втором
    прогоне, — краш-петля, которую он будет вечно поддерживать."""
    path = tmp_path / "twice.db"
    Store(str(path)).add_message("c1", "user", "раз", ts=1.0)
    again = Store(str(path))
    again.add_message("c1", "user", "два", ts=2.0)
    assert len(again.history("c1")) == 2


# ── ГРАНИЦА, КОТОРУЮ ПРАВКА НЕ ДОЛЖНА БЫЛА СДВИНУТЬ ─────────────────────────

def test_v_api_uezzhaet_TOLKO_rol_i_tekst():
    """`author` НЕ смеет попасть в вызов Anthropic.

    `build_messages` мапит историю в сообщения API напрямую. Роли 'human' в
    том API нет, а лишний ключ в элементе — это отказ вызова. Пин на СОСТАВ
    ключей, а не на их наличие: «пробросим всё» — первая же правка, которая
    это сломает."""
    history = [
        {"role": "user", "text": "вопрос", "ts": 1.0, "author": "bot"},
        {"role": "assistant", "text": "ответ", "ts": 2.0, "author": "human"},
    ]
    out = build_messages(history)
    assert out == [
        {"role": "user", "content": "вопрос"},
        {"role": "assistant", "content": "ответ"},
    ]
    for item in out:
        assert set(item) == {"role", "content"}


def test_perehvat_pishet_author_human_v_samom_rannere():
    """Вызов на месте перехвата обязан нести author='human'.

    Юнит выше проверяет ХРАНИЛИЩЕ; этот — что раннер им пользуется. Без него
    колонка есть, тесты зелёные, а в боевой базе по-прежнему всё 'bot'.
    Разбор через AST, а не поиск подстроки: подстрока совпала бы и в
    комментарии."""
    tree = ast.parse(_RUNNER.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "add_message"):
            continue
        authors = [kw.value.value for kw in node.keywords
                   if kw.arg == "author" and isinstance(kw.value, ast.Constant)]
        found.append(authors)

    assert found, "в раннере не осталось ни одного add_message — правило устарело"
    assert ["human"] in found, (
        "ни один add_message в telethon_run.py не помечает автора как human: "
        "ручные сообщения владельца снова неотличимы от бота")
