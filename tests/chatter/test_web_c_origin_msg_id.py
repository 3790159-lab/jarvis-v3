# -*- coding: utf-8 -*-
"""Сторожа 28-33 спеки «ВЕБ, волна 2 / пара C» — `origin_msg_id` в TEXT.

Спека: `docs/superpowers/specs/2026-08-28-web-c-envelope-identity.md`,
§1.4, §5, §9 (сторожа 28-33).

ПРЕМИСА ПОПРАВЛЕНА ЗАМЕРОМ, И ЭТО ВАЖНО ДЛЯ ТОГО, ЧТО ЗДЕСЬ МЕРЯЕТСЯ (§1.4):
`origin_msg_id` НИКОГДА не был id сообщения канала — это `MAX(messages.id)`,
наш локальный AUTOINCREMENT. Значит дыры «веб сломает выставление ТИПОМ
колонки» сегодня нет, а настоящая дыра идемпотентности лежит ВЫШЕ колонки и
чинится очередью волны 3. Здесь чинится другое, и оно названо §5.1 прямо:

🔴 SQLite типизирует динамически. Колонка с INTEGER-аффинностью строку
`"wamid.HBg…"` проглотит, но `"007"` приведёт к числу 7 — и `"007"` с `"7"`
станут ОДНИМ ключом идемпотентности. Два разных сообщения канала схлопнутся,
второй счёт будет молча подавлен как дубль. Это ровно тот класс, ради которого
арка и пишется: правдоподобный неверный ответ вместо отказа.

ОКНО ИЗМЕРЕНО ОТКРЫТЫМ (§1.4, 28.08): `quotes` — 0 строк во всех трёх базах,
`invoices` — 1 строка. Другого такого окна не будет.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chatter.payments.money import Money
from chatter.storage.db import Store

REBUILT = ("quotes", "invoices")
CID = "telegram:8849893367:volska"


# ═══ оснастка ═══════════════════════════════════════════════════════════════

def _declared_type(conn, table: str, col: str) -> str:
    for r in conn.execute("PRAGMA table_info(%s)" % table):
        if r[1] == col:
            return (r[2] or "").upper()
    raise AssertionError("в таблице %s нет колонки %s" % (table, col))


def _unique_index_cols(conn, table: str) -> list[list[str]]:
    out = []
    for row in conn.execute("PRAGMA index_list(%s)" % table):
        name, unique = row[1], row[2]
        if not unique:
            continue
        out.append([r[2] for r in conn.execute("PRAGMA index_info(%s)" % name)])
    return out


def _require_text_column(conn) -> None:
    """Предпосылка сторожей 29-31: колонка УЖЕ перестроена в TEXT.

    Без неё сторожа ниже зелены по построению на сегодняшней INTEGER-колонке:
    «два вызова с одним ключом дали один счёт» верно и там. Сторож, зелёный до
    арки, ничего о ней не утверждает."""
    for table in REBUILT:
        got = _declared_type(conn, table, "origin_msg_id")
        assert got == "TEXT", (
            "`%s.origin_msg_id` объявлен %r, а не TEXT — перестройки §5.2 ещё "
            "нет, и всё, что ниже, проверяет не тот вопрос." % (table, got))


def _new_invoice(store: Store, origin, *, now=1.0):
    return store.create_invoice(
        contact_id=CID, origin_msg_id=origin, amount=Money(40000, "USD"),
        channel_id=None, due_ts=None, created_by="bot", amount_source="quote",
        status="issued", now=now)


def _legacy_integer_tables(path: Path) -> None:
    """Сделать `quotes`/`invoices` СТАРЫМИ (origin_msg_id INTEGER) — стенд
    «база до перестройки».

    DDL берётся из самой базы и правится ОДНОЙ подстановкой, а не пишется
    литералом: литеральный DDL устарел бы от первой же новой колонки и стал бы
    красить сторожа по своей вине. Строим здесь только СОСТОЯНИЕ ДО — само
    утверждение сторожа литерально и лежит в assert'ах."""
    conn = sqlite3.connect(str(path))
    try:
        for table in REBUILT:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            assert row, "в базе нет таблицы %s" % table
            ddl = row[0]
            legacy = ddl.replace("origin_msg_id     TEXT", "origin_msg_id     INTEGER")
            legacy = legacy.replace("origin_msg_id        TEXT",
                                    "origin_msg_id        INTEGER")
            legacy = legacy.replace("origin_msg_id TEXT", "origin_msg_id INTEGER")
            conn.execute("DROP TABLE %s" % table)
            conn.execute(legacy)
        conn.commit()
    finally:
        conn.close()


# ═══ Сторож 28 ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("table", REBUILT)
def test_guard28_obyavlennyy_tip_TEXT_i_UNIQUE_na_meste(tmp_path, table):
    """Сторож 28: после перестройки объявленный тип `origin_msg_id` в `quotes`
    и `invoices` — `TEXT`, и `UNIQUE (contact_id, origin_msg_id)` СУЩЕСТВУЕТ.

    Пин по индексам/DDL, а не по «мы же скопировали»: в SQLite `ALTER COLUMN`
    нет, таблица перестраивается целиком, и ограничение не «сохраняется», а
    ОБЪЯВЛЯЕТСЯ ЗАНОВО вместе с таблицей (§5.2 п.1). Потерять его при этом —
    одна забытая строка, и потеря молчаливая: идемпотентность выставления
    перестанет существовать, а таблица будет выглядеть прежней."""
    with Store(tmp_path / "types.db") as s:
        got = _declared_type(s._conn, table, "origin_msg_id")
        assert got == "TEXT", (
            "`%s.origin_msg_id` объявлен %r, ждали TEXT. INTEGER-аффинность "
            "приведёт '007' к числу 7, и '007' с '7' станут ОДНИМ ключом "
            "идемпотентности: второй счёт будет молча подавлен как дубль."
            % (table, got))
        uniques = _unique_index_cols(s._conn, table)
        assert ["contact_id", "origin_msg_id"] in uniques, (
            "в `%s` нет UNIQUE-индекса на (contact_id, origin_msg_id); есть "
            "%r. Перестройка потеряла ограничение — ретрай пайплайна породит "
            "второй счёт, и таблица при этом будет выглядеть прежней."
            % (table, uniques))


@pytest.mark.parametrize("table, index", [("quotes", "idx_quotes_contact"),
                                          ("invoices", "idx_invoices_contact")])
def test_guard28_indeksy_po_kontaktu_sozdany_zanovo(tmp_path, table, index):
    """Сторож 28, вторая половина: индексы по `contact_id` тоже на месте.

    §5.2 п.4 называет это отдельным шагом: индексы УХОДЯТ ВМЕСТЕ С ТАБЛИЦЕЙ, и
    перестройка, забывшая их пересоздать, ничего не ломает — она просто делает
    ленту панели медленнее с каждым месяцем, и заметят это не скоро."""
    with Store(tmp_path / "idx.db") as s:
        names = {r[1] for r in s._conn.execute("PRAGMA index_list(%s)" % table)}
        assert index in names, (
            "индекс %s не пересоздан после перестройки %s (есть %r). Индексы "
            "уходят вместе с таблицей (§5.2 п.4)." % (index, table, sorted(names)))


# ═══ Сторож 29 ══════════════════════════════════════════════════════════════

def test_guard29_idempotentnost_vystavleniya_zhiva(tmp_path):
    """Сторож 29: два вызова `create_invoice` с одним `(contact_id,
    origin_msg_id)` дают ОДИН счёт.

    Свойство, ради сохранности которого вся §5 и написана: ретрай пайплайна не
    имеет права породить второй счёт — фантомы съедают
    `per_contact_invoice_cap`, и владелец видит три счёта там, где выставлял
    один."""
    with Store(tmp_path / "idem.db") as s:
        _require_text_column(s._conn)
        s.get_or_create_contact(CID)
        first = _new_invoice(s, "11")
        again = _new_invoice(s, "11", now=2.0)
        assert first["invoice_id"] == again["invoice_id"], (
            "два вызова с одним ключом дали РАЗНЫЕ счета (%r и %r): "
            "идемпотентность выставления не пережила перестройку."
            % (first["invoice_id"], again["invoice_id"]))
        n = s._conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        assert n == 1, "в таблице %d счетов вместо одного" % n


# ═══ Сторож 30 ══════════════════════════════════════════════════════════════

def test_guard30_lovushka_5_3_vyzyvatel_peredal_int(tmp_path):
    """Сторож 30 (ЛОВУШКА §5.3 — самый опасный шов этой правки).

    🔴 `create_invoice` ищет дубль как `WHERE contact_id=? AND origin_msg_id=?`.
    В SQLite целое `7` и строка `"7"` НЕ РАВНЫ НИКОГДА. Значит после
    перестройки вызыватель, продолжающий передавать `int`, существующего счёта
    НЕ НАЙДЁТ — и выставит ВТОРОЙ. Идемпотентность не сломается заметно; она
    перестанет существовать МОЛЧА.

    Пин именно на «нашёлся существующий счёт», а не на «вызов не упал»: вызов
    не упадёт ни в одном из двух исходов. §5.3 требует приведение к строке в
    ОДНОМ месте — на границе `Store`, — потому что два приведения в двух
    местах это два числа на одну вещь."""
    with Store(tmp_path / "int.db") as s:
        _require_text_column(s._conn)
        s.get_or_create_contact(CID)
        first = _new_invoice(s, 7)                 # int — как звал прежний код
        again = _new_invoice(s, 7, now=2.0)
        assert first["invoice_id"] == again["invoice_id"], (
            "вызыватель передал int 7 дважды и получил ДВА счёта (%r и %r). "
            "Граница `Store` не привела значение к строке — идемпотентность "
            "выставления перестала существовать молча."
            % (first["invoice_id"], again["invoice_id"]))
        mixed = _new_invoice(s, "7", now=3.0)
        assert mixed["invoice_id"] == first["invoice_id"], (
            "int 7 и строка '7' дали РАЗНЫЕ счета (%r и %r): приведение "
            "делается не в одном месте, и одно и то же сообщение живёт под "
            "двумя ключами." % (first["invoice_id"], mixed["invoice_id"]))
        row = s._conn.execute(
            "SELECT typeof(origin_msg_id) t FROM invoices").fetchone()
        assert row["t"] == "text", (
            "в колонке лежит %r, а не 'text': значение хранится тем типом, "
            "которым его передали, и `WHERE origin_msg_id=?` будет сходиться "
            "через раз." % (row["t"],))


@pytest.mark.parametrize("bad, why", [
    (7.0, "REAL: приведение НЕОБРАТИМО — 7.0 даёт ключ '7.0', а не '7', то "
          "есть тихо создаёт ВТОРОЙ счёт на то же сообщение"),
    (sqlite3.Binary(b"\x00\x01"), "BLOB: текстового ключа у него нет вовсе"),
])
def test_guard30_granitsa_Store_privodit_int_no_otkazyvaet_na_ostalnom(tmp_path, bad, why):
    """Сторож 30, ВСТРЕЧНАЯ ПОЛОВИНА (решение владельца 28.08): на границе
    `Store` приводится `int` — и ТОЛЬКО он. Всё остальное — отказ.

    Почему именно так, и почему это не придирка. `int` — законный сегодняшний
    вызыватель (`payments/dialogue.py` шлёт `msg_id: int | None`, источник —
    `MAX(messages.id)`), и его приведение ОБРАТИМО: `7` и `'7'` — один ключ.
    `REAL` и `BLOB` необратимы: `7.0` становится `'7.0'`, то есть ДРУГИМ
    ключом, и существующий счёт по нему не найдётся — идемпотентность не
    сломается заметно, она перестанет существовать молча.

    Отказ — `ValueError`: это отказ ЗНАЧЕНИЯ аргумента, а не состояния базы
    (состояние отказывает `RuntimeError`, сторожа 25, 26, 33). Терпимая
    граница, молча приводящая что угодно, — это и есть второе приведение в
    втором месте, то есть два числа на одну вещь."""
    with Store(tmp_path / "coerce.db") as s:
        _require_text_column(s._conn)
        s.get_or_create_contact(CID)
        with pytest.raises(ValueError):
            _new_invoice(s, bad)
        n = s._conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        assert n == 0, (
            "отказ случился ПОСЛЕ записи: счёт с неприводимым ключом (%s) уже "
            "лежит в таблице. Убирать его придётся человеку, а ключ "
            "идемпотентности у него всё равно неверный." % why)


# ═══ Сторож 31 ══════════════════════════════════════════════════════════════

def test_guard31_007_i_7_eto_RAZNYE_klyuchi(tmp_path):
    """Сторож 31: `"007"` и `"7"` — РАЗНЫЕ ключи идемпотентности.

    §5.1 называет это причиной перестройки дословно. И называет ловушку самого
    сторожа: под INTEGER этот тест зелёный по построению — там `"007"`
    приводится к 7 и второй счёт честно подавляется как дубль, то есть
    «идемпотентность работает». Под TEXT он настоящий."""
    with Store(tmp_path / "leading.db") as s:
        _require_text_column(s._conn)
        s.get_or_create_contact(CID)
        seven = _new_invoice(s, "7")
        oh_seven = _new_invoice(s, "007", now=2.0)
        assert seven["invoice_id"] != oh_seven["invoice_id"], (
            "'7' и '007' схлопнулись в один счёт (%r): два РАЗНЫХ сообщения "
            "канала стали одним ключом, и второй счёт молча подавлен как "
            "дубль. Это и есть правдоподобный неверный ответ вместо отказа."
            % (seven["invoice_id"],))
        n = s._conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        assert n == 2, "счетов %d, ждали два" % n


# ═══ Сторож 32 ══════════════════════════════════════════════════════════════

def test_guard32_perestroyka_ne_tronula_dannye(tmp_path):
    """Сторож 32: перестройка не тронула данные — единственная живая строка
    `invoices` сохранила ВСЕ свои значения, а `origin_msg_id` стал текстом
    того же числа.

    Замер §1.4 нашёл ровно эту строку в живой базе demo
    (`typeof(origin_msg_id) = 'integer'`). Перестройка таблицы — это
    `CREATE ... INSERT ... SELECT ... DROP ... RENAME`, и потерять в ней
    колонку или перепутать порядок значений можно молча: строк столько же,
    типы верные, а сумма уехала в другую колонку."""
    db = tmp_path / "keep.db"
    with Store(db) as s:
        s.get_or_create_contact(CID)
    _legacy_integer_tables(db)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO invoices (invoice_id, contact_id, currency, status,"
            " created_ts, created_by, origin_msg_id, amount_total, amount_source)"
            " VALUES ('INV-volska-000001', ?, 'USD', 'issued', 1000.0, 'bot', 42,"
            " 40000, 'quote')", (CID,))
        conn.commit()
        before = dict(zip(
            ("invoice_id", "contact_id", "currency", "status", "created_ts",
             "created_by", "amount_total", "amount_source"),
            conn.execute(
                "SELECT invoice_id, contact_id, currency, status, created_ts,"
                " created_by, amount_total, amount_source FROM invoices").fetchone()))
    finally:
        conn.close()

    with Store(db) as s:
        assert _declared_type(s._conn, "invoices", "origin_msg_id") == "TEXT", (
            "перестройки не произошло вовсе: колонка осталась INTEGER, и "
            "сторож «данные целы» ничего не утверждает.")
        row = s._conn.execute("SELECT * FROM invoices").fetchone()
        got = {k: row[k] for k in before}
        assert got == before, (
            "перестройка изменила значения строки:\n  было %r\n  стало %r"
            % (before, got))
        assert row["origin_msg_id"] == "42", (
            "origin_msg_id стал %r, ждали строку '42' — тот же номер, только "
            "текстом." % (row["origin_msg_id"],))
        t = s._conn.execute(
            "SELECT typeof(origin_msg_id) t FROM invoices").fetchone()["t"]
        assert t == "text", (
            "typeof(origin_msg_id) = %r: значение осталось числом в TEXT-"
            "колонке, и `WHERE origin_msg_id='42'` его не найдёт." % (t,))


# ═══ Сторож 33 ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad, why", [
    (7.0, "REAL: CAST даёт '7.0', то есть ДРУГОЙ ключ, а не тот же номер"),
    (sqlite3.Binary(b"\x00\x01"), "BLOB: текстового представления, по которому "
                                  "искали бы дубль, у него нет"),
])
def test_guard33_okno_zakryto_chestno_neprivodimye_stroki_eto_otkaz(tmp_path, bad, why):
    """Сторож 33: перестройка на таблице со строками, которые НЕ ПРИВОДЯТСЯ, —
    отказ, база не тронута (образец `_assert_payments_rebuild_window`).

    Дом уже записал это правило для `payments`: перестраивать, пока строк нет,
    и ОТКАЗЫВАТЬСЯ, когда появились. Здесь окно шире (одна строка на три базы,
    §1.4), но принцип тот же: значение, которое перестройка не может перенести
    БЕЗ ПОТЕРИ СМЫСЛА, обязано остановить её громко, а не проехать `CAST`-ом.

    ЧТО СЧИТАЕТСЯ «НЕПРИВОДИМЫМ» — решено владельцем 28.08: значение, у
    которого текстовое представление НЕ РАВНО исходному номеру (`REAL`: 7.0 →
    '7.0') либо которого нет вовсе (`BLOB`). Оба хранимы в INTEGER-аффинной
    колонке сегодня, и оба меняют ключ идемпотентности МОЛЧА — то есть ровно
    тот класс, ради которого §5 и написана. `int` в этот список не входит: его
    приведение обратимо, и `Store` обязан его привести (сторож 30)."""
    db = tmp_path / "window.db"
    with Store(db) as s:
        s.get_or_create_contact(CID)
    _legacy_integer_tables(db)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO invoices (invoice_id, contact_id, currency, status,"
            " created_ts, created_by, origin_msg_id)"
            " VALUES ('INV-volska-000001', ?, 'USD', 'issued', 1000.0, 'bot', ?)",
            (CID, bad))
        conn.commit()
    finally:
        conn.close()
    before = db.read_bytes()

    with pytest.raises(RuntimeError):
        # `RuntimeError`, как и у миграции §4 (решение владельца 28.08): окно
        # перестройки — это состояние ТАБЛИЦЫ, а не значение аргумента.
        # Образец `_assert_payments_rebuild_window` стоит на том же месте.
        with Store(db):
            pass
    assert db.read_bytes() == before, (
        "после отказа база изменилась побайтово. Образец "
        "`_assert_payments_rebuild_window`: «Миграция остановлена, база не "
        "тронута».")
