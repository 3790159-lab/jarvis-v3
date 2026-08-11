"""Хранилище счетов и перестройка `payments` (спека §3.1, §14 пп.1-3, 15).

Окно перестройки открыто ровно один раз: на 2026-08-10 в боевой `.secrets/demo.db`
`payments` содержит 0 строк, и `control_events` с kind='payment' — тоже 0. Пока
это так, `amount REAL` можно заменить на минорные INTEGER без дуального чтения
в отчётности НАВСЕГДА. Появится хоть одна строка — миграция обязана
ОСТАНОВИТЬСЯ, а не «как-нибудь сконвертировать»: округление чужих денег втихую
хуже, чем упавший старт.
"""
from __future__ import annotations

import sqlite3

import pytest

from chatter.payments.model import PaymentRecord, make_dedup_key
from chatter.payments.money import Money, from_major
from chatter.storage.db import PaymentsMigrationBlocked, Store

USD = "USD"

# Схема `payments` ДО перестройки — ровно как в проде на 2026-08-10.
_OLD_PAYMENTS = """
CREATE TABLE payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id  TEXT NOT NULL,
    card_msg_id INTEGER,
    amount      REAL,
    currency    TEXT NOT NULL DEFAULT 'USD',
    ts          REAL NOT NULL,
    source      TEXT NOT NULL,
    UNIQUE (contact_id, card_msg_id)
);
"""


def _legacy_db(path, *, rows: int = 0):
    conn = sqlite3.connect(str(path))
    conn.executescript(_OLD_PAYMENTS)
    for i in range(rows):
        conn.execute("INSERT INTO payments(contact_id, card_msg_id, amount, currency, ts, source)"
                     " VALUES (?,?,?,?,?,?)", (f"c{i}:volska", i, 900.0, USD, 1.0, "card_button"))
    conn.commit()
    conn.close()
    return path


def _cols(store, table):
    return {r["name"] for r in store._conn.execute(f"PRAGMA table_info({table})")}


# ── перестройка payments ───────────────────────────────────────────────────

def test_fresh_db_gets_the_final_schema(tmp_path):
    with Store(tmp_path / "new.db") as s:
        cols = _cols(s, "payments")
    assert {"dedup_key", "amount_minor", "amount_received", "channel_id",
            "confirmed_by", "invoice_id", "stage_no", "external_event_id",
            "currency_received"} <= cols
    # Старые поля не тащатся «для истории»: истории нет (0 строк в проде).
    assert not ({"card_msg_id", "amount", "source"} & cols)


def test_legacy_empty_table_is_rebuilt(tmp_path):
    db = _legacy_db(tmp_path / "old.db", rows=0)
    with Store(db) as s:
        cols = _cols(s, "payments")
    assert "dedup_key" in cols and "card_msg_id" not in cols


def test_rebuild_makes_a_backup_first(tmp_path):
    db = _legacy_db(tmp_path / "old.db", rows=0)
    with Store(db):
        pass
    backups = list(tmp_path.glob("old.db.pre-payments-*.bak"))
    assert len(backups) == 1, "перестройка обязана оставить снимок ДО"
    # Снимок — со СТАРОЙ схемой: иначе откатываться некуда.
    conn = sqlite3.connect(str(backups[0]))
    old_cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)")}
    conn.close()
    assert "card_msg_id" in old_cols


def test_rebuild_refuses_when_rows_exist(tmp_path):
    """Окно закрыто — стоп и доклад. Молча сконвертировать чужие деньги нельзя."""
    db = _legacy_db(tmp_path / "old.db", rows=1)
    with pytest.raises(PaymentsMigrationBlocked, match="1"):
        Store(db)


def test_refused_migration_leaves_the_database_untouched(tmp_path):
    db = _legacy_db(tmp_path / "old.db", rows=2)
    with pytest.raises(PaymentsMigrationBlocked):
        Store(db)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(payments)")}
    n = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    conn.close()
    assert "card_msg_id" in cols and n == 2, "отказ обязан быть без побочных эффектов"


def test_rebuild_is_idempotent_across_restarts(tmp_path):
    """Гардиан перезапускает раннер постоянно: миграция, падающая на втором
    прогоне, — это краш-петля, которую он будет вечно поддерживать."""
    db = _legacy_db(tmp_path / "old.db", rows=0)
    with Store(db):
        pass
    with Store(db):
        pass
    assert len(list(tmp_path.glob("old.db.pre-payments-*.bak"))) == 1


# ── идемпотентность записи оплаты ──────────────────────────────────────────

def _rec(store, key, *, amount=90000, contact="c1:volska", **kw):
    store.get_or_create_contact(contact)
    store.record_payment(PaymentRecord(
        contact_id=contact, dedup_key=key, ts=1.0, confirmed_by="owner",
        amount=None if amount is None else Money(amount, USD), **kw))


def test_two_taps_on_the_same_card_are_one_payment(tmp_path):
    with Store(tmp_path / "d.db") as s:
        _rec(s, make_dedup_key("tap", 42), amount=None)
        _rec(s, make_dedup_key("tap", 42), amount=90000)
        rows = s.payments_between(0.0, 1e12)
    assert len(rows) == 1 and rows[0]["amount_minor"] == 90000


def test_two_panel_payments_are_two_rows(tmp_path):
    """СЕГОДНЯШНИЙ ДЕФЕКТ: у веб-панели нет личности события, все её оплаты по
    одному контакту делят сентинел 0 и схлопываются в одну. С panel:<token>
    они наконец различимы."""
    with Store(tmp_path / "d.db") as s:
        _rec(s, make_dedup_key("panel", "tok-1"), amount=30000)
        _rec(s, make_dedup_key("panel", "tok-2"), amount=40000)
        rows = s.payments_between(0.0, 1e12)
    assert len(rows) == 2
    assert sum(r["amount_minor"] for r in rows) == 70000


def test_different_sources_never_collide(tmp_path):
    with Store(tmp_path / "d.db") as s:
        _rec(s, "tap:7", amount=10000)
        _rec(s, "panel:7", amount=20000)
        _rec(s, "evt:wise:7", amount=30000)
        assert len(s.payments_between(0.0, 1e12)) == 3


def test_same_key_different_contacts_are_separate(tmp_path):
    with Store(tmp_path / "d.db") as s:
        _rec(s, "tap:1", contact="a:volska", amount=10000)
        _rec(s, "tap:1", contact="b:volska", amount=20000)
        assert len(s.payments_between(0.0, 1e12)) == 2


def test_unique_index_exists_in_the_database_itself(tmp_path):
    """Обещание идемпотентности, которое держится только кодом, — это обещание
    до первого нового вызывателя."""
    with Store(tmp_path / "d.db") as s:
        with pytest.raises(sqlite3.IntegrityError):
            s._conn.execute(
                "INSERT INTO payments(contact_id, dedup_key, ts, confirmed_by, currency)"
                " VALUES ('x','tap:1',1.0,'owner','USD'), ('x','tap:1',2.0,'owner','USD')")


# ── котировка, счёт, ступень ───────────────────────────────────────────────

def test_invoice_writes_exactly_one_stage_in_phase0(tmp_path):
    """§14 п.3: Ф1 — это «строк больше одной», а не бэкфилл и вечная ветка
    «счета без ступеней»."""
    with Store(tmp_path / "d.db") as s:
        s.get_or_create_contact("c1:volska")
        inv = s.create_invoice(contact_id="c1:volska", origin_msg_id=11,
                               amount=Money(40000, USD), channel_id="iban_eur",
                               due_ts=100.0, created_by="bot",
                               amount_source="price_upper", status="issued", now=1.0)
        stages = s.invoice_stages(inv["invoice_id"])
    assert len(stages) == 1
    assert stages[0]["stage_no"] == 1 and stages[0]["amount_due"] == 40000


def test_invoice_issuing_is_idempotent_on_origin_msg_id(tmp_path):
    """§14 п.15: ретрай пайплайна не имеет права выставить второй счёт —
    фантомы съели бы per_contact_invoice_cap ещё до живого лида."""
    with Store(tmp_path / "d.db") as s:
        s.get_or_create_contact("c1:volska")
        kw = dict(contact_id="c1:volska", origin_msg_id=11, amount=Money(40000, USD),
                  channel_id="iban_eur", due_ts=100.0, created_by="bot",
                  amount_source="price_upper", status="issued", now=1.0)
        a = s.create_invoice(**kw)
        b = s.create_invoice(**kw)
        assert a["invoice_id"] == b["invoice_id"]
        assert len(s.invoices_for(contact_id="c1:volska")) == 1
        assert len(s.invoice_stages(a["invoice_id"])) == 1


def test_quote_history_is_append_only(tmp_path):
    """§2.3: без истории торга бот на повторный заход назовёт другую цену тому
    же лиду, и это будет видно клиенту."""
    with Store(tmp_path / "d.db") as s:
        s.get_or_create_contact("c1:volska")
        q0 = s.create_quote(contact_id="c1:volska", position_id="logo_create",
                            step_idx=0, amount=Money(40000, USD), scope_key="logo_full",
                            amount_source="price_upper", knowledge_version="v1",
                            origin_msg_id=11, now=1.0)
        q1 = s.create_quote(contact_id="c1:volska", position_id="logo_create",
                            step_idx=1, amount=Money(37500, USD), scope_key="logo_s375",
                            amount_source="ladder_step", knowledge_version="v1",
                            origin_msg_id=12, now=2.0)
        rows = s.quotes_for("c1:volska")
    assert [r["step_idx"] for r in rows] == [0, 1]
    assert [r["status"] for r in rows] == ["superseded", "active"]
    assert q0["quote_id"] != q1["quote_id"]


# ── статус как проекция ────────────────────────────────────────────────────

def _invoice(s, total=40000, due=100.0):
    s.get_or_create_contact("c1:volska")
    return s.create_invoice(contact_id="c1:volska", origin_msg_id=11,
                            amount=Money(total, USD), channel_id="iban_eur",
                            due_ts=due, created_by="bot", amount_source="price_upper",
                            status="issued", now=1.0)


def test_full_payment_settles_the_invoice(tmp_path):
    with Store(tmp_path / "d.db") as s:
        inv = _invoice(s)
        s.apply_payment(PaymentRecord(
            contact_id="c1:volska", dedup_key="tap:1", ts=5.0, confirmed_by="owner",
            amount=Money(40000, USD), invoice_id=inv["invoice_id"], stage_no=1), now=5.0)
        got = s.get_invoice(inv["invoice_id"])
    assert got["status"] == "paid"
    assert s_is_settled(got["status"])
    assert got["first_payment_ts"] == 5.0


def s_is_settled(status):
    from chatter.payments.statuses import is_settled
    return is_settled(status)


def test_partial_payment_leaves_a_remainder(tmp_path):
    with Store(tmp_path / "d.db") as s:
        inv = _invoice(s)
        s.apply_payment(PaymentRecord(
            contact_id="c1:volska", dedup_key="tap:1", ts=5.0, confirmed_by="owner",
            amount=Money(15000, USD), invoice_id=inv["invoice_id"], stage_no=1), now=5.0)
        assert s.get_invoice(inv["invoice_id"])["status"] == "partially_paid"
        assert s.remaining_minor(inv["invoice_id"]) == 25000
        assert not s_is_settled(s.get_invoice(inv["invoice_id"])["status"])


def test_first_payment_ts_records_the_first_not_the_last(tmp_path):
    """Вопрос §12 «closed по полной оплате или по первой» решится позже —
    и решится без бэкфилла, только если пишем с Ф0."""
    with Store(tmp_path / "d.db") as s:
        inv = _invoice(s)
        for key, amt, ts in (("tap:1", 15000, 5.0), ("tap:2", 25000, 9.0)):
            s.apply_payment(PaymentRecord(
                contact_id="c1:volska", dedup_key=key, ts=ts, confirmed_by="owner",
                amount=Money(amt, USD), invoice_id=inv["invoice_id"], stage_no=1), now=ts)
        got = s.get_invoice(inv["invoice_id"])
    assert got["first_payment_ts"] == 5.0
    assert got["status"] == "paid"


def test_repeated_tap_does_not_double_the_received_sum(tmp_path):
    """Идемпотентность обязана держаться и на уровне ДЕНЕГ, а не только строк:
    ровно так выручка 900 превращалась в 1800 (§7.1)."""
    with Store(tmp_path / "d.db") as s:
        inv = _invoice(s, total=90000)
        for _ in range(3):
            s.apply_payment(PaymentRecord(
                contact_id="c1:volska", dedup_key="tap:42", ts=5.0, confirmed_by="owner",
                amount=Money(90000, USD), invoice_id=inv["invoice_id"], stage_no=1), now=5.0)
        assert s.received_minor(inv["invoice_id"]) == 90000
        assert s.get_invoice(inv["invoice_id"])["status"] == "paid"


def test_overpayment_is_not_silently_accepted_as_paid(tmp_path):
    with Store(tmp_path / "d.db") as s:
        inv = _invoice(s)
        s.apply_payment(PaymentRecord(
            contact_id="c1:volska", dedup_key="tap:1", ts=5.0, confirmed_by="owner",
            amount=Money(45000, USD), invoice_id=inv["invoice_id"], stage_no=1), now=5.0)
        assert s.get_invoice(inv["invoice_id"])["status"] == "overpaid"


def test_payment_without_an_invoice_still_records(tmp_path):
    """Ручной путь «владелец отметил оплату вне счёта» существует и в Ф0 —
    ломать его нельзя (§0.1)."""
    with Store(tmp_path / "d.db") as s:
        _rec(s, "tap:1", amount=90000)
        rows = s.payments_between(0.0, 1e12)
    assert len(rows) == 1 and rows[0]["invoice_id"] is None


def test_issued_invoice_cannot_exist_without_an_amount(tmp_path):
    """§14 п.6: NULL-сумма допустима ТОЛЬКО в draft/awaiting_owner. Выставленный
    счёт без суммы — это «оплатіть, будь ласка» без числа."""
    with Store(tmp_path / "d.db") as s:
        s.get_or_create_contact("c1:volska")
        with pytest.raises(ValueError, match="draft/awaiting_owner"):
            s.create_invoice(contact_id="c1:volska", origin_msg_id=1, amount=None,
                             channel_id="iban_eur", due_ts=100.0, created_by="bot",
                             amount_source=None, status="issued", now=1.0)
        inv = s.create_invoice(contact_id="c1:volska", origin_msg_id=2, amount=None,
                               channel_id="iban_eur", due_ts=100.0, created_by="bot",
                               amount_source=None, status="awaiting_owner", now=1.0)
    assert inv["amount_total"] is None


# ── выбранная ступень объёма едет в котировку идентификатором ──────────────

def test_a_quote_carries_the_chosen_tier_id(tmp_path):
    """Идентификатор, а не текст: по нему счёт узнаёт, за какой объём выставлен.
    Восстанавливать ступень из scope_key значило бы завести второй способ
    узнать то же самое — заготовку следующего расхождения."""
    s = Store(str(tmp_path / "q.db"))
    row = s.create_quote(
        contact_id="1:demo", position_id="logo", step_idx=0,
        amount=from_major(300, "USD"), scope_key="tier_basic",
        amount_source="tier_selected", tier_id="basic",
        knowledge_version="k", origin_msg_id=1, now=1.0)
    assert row["tier_id"] == "basic"
    assert row["amount_source"] == "tier_selected"


def test_a_quote_without_a_tier_keeps_it_null(tmp_path):
    """Объём не назван — ступени нет. NULL здесь честнее любого дефолта: он
    отличим от «выбрали базовый»."""
    s = Store(str(tmp_path / "q2.db"))
    row = s.create_quote(
        contact_id="1:demo", position_id="logo", step_idx=0,
        amount=from_major(400, "USD"), scope_key="full",
        amount_source="price_upper", knowledge_version="k",
        origin_msg_id=1, now=1.0)
    assert row["tier_id"] is None


def test_choosing_another_tier_supersedes_the_previous_quote(tmp_path):
    """Смена ступени вверх = новая котировка, старая superseded (решение
    владельца). Две активные означали бы, что «что мы ему называли» перестало
    иметь ответ."""
    s = Store(str(tmp_path / "q3.db"))
    s.create_quote(contact_id="1:demo", position_id="logo", step_idx=0,
                   amount=from_major(300, "USD"), scope_key="tier_basic",
                   amount_source="tier_selected", tier_id="basic",
                   knowledge_version="k", origin_msg_id=1, now=1.0)
    s.create_quote(contact_id="1:demo", position_id="logo", step_idx=0,
                   amount=from_major(400, "USD"), scope_key="tier_std",
                   amount_source="tier_selected", tier_id="standard",
                   knowledge_version="k", origin_msg_id=2, now=2.0)
    rows = s.quotes_for("1:demo")
    assert [(r["tier_id"], r["status"]) for r in rows] == [
        ("basic", "superseded"), ("standard", "active")]


def test_tier_id_is_migrated_into_an_existing_base(tmp_path):
    """Гардиан перезапускает раннер постоянно: миграция обязана быть
    идемпотентной, а не падать на втором прогоне."""
    path = str(tmp_path / "old.db")
    Store(path).close()
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE quotes DROP COLUMN tier_id")
    conn.commit()
    conn.close()

    s = Store(path)
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(quotes)")}
    assert "tier_id" in cols
    Store(path).close()          # второй прогон не падает


# ── имя лида: кэш в contacts, чтобы веб не ходил в Telethon ───────────────

def test_display_name_is_stored_and_read_back(tmp_path):
    s = Store(str(tmp_path / "n.db"))
    s.get_or_create_contact("1:demo")
    s.set_display_name("1:demo", "Олена")
    assert s.get_or_create_contact("1:demo")["display_name"] == "Олена"


def test_an_unknown_contact_has_no_name_rather_than_an_empty_string(tmp_path):
    """NULL отличим от «имя стёрли». Пустая строка означала бы, что мы уже
    спрашивали и получили пустоту."""
    s = Store(str(tmp_path / "n2.db"))
    assert s.get_or_create_contact("2:demo")["display_name"] is None


def test_the_name_is_not_overwritten_by_a_blank(tmp_path):
    """Telethon иногда не знает сущность. «Не знаю сейчас» не должно стирать
    то, что мы уже узнали раньше."""
    s = Store(str(tmp_path / "n3.db"))
    s.get_or_create_contact("3:demo")
    s.set_display_name("3:demo", "Олена")
    s.set_display_name("3:demo", "   ")
    assert s.get_or_create_contact("3:demo")["display_name"] == "Олена"


def test_display_name_is_migrated_into_an_existing_base(tmp_path):
    path = str(tmp_path / "old2.db")
    Store(path).close()
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE contacts DROP COLUMN display_name")
    conn.commit()
    conn.close()
    s = Store(path)
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(contacts)")}
    assert "display_name" in cols
    Store(path).close()          # второй прогон не падает
