"""Метрики клиентского дашборда (CLIENT_SCREENS.md §6.4)."""
from __future__ import annotations

import sqlite3

import pytest

from app.services import tamapi_metrics as M
from chatter.payments.model import PaymentRecord, make_dedup_key
from chatter.payments.money import from_major
from chatter.storage.db import Store

NOW = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "d.db"
    s = Store(str(p))
    for i in range(3):
        cid = f"{100+i}:demo"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "привіт", ts=NOW - 2 * DAY)
        s.add_message(cid, "assistant", "вітаю", ts=NOW - 2 * DAY + 60)
        s.add_message(cid, "user", "скільки?", ts=NOW - DAY)
    # один квалифицирован, один передан, один оплачен
    s.record_transition("100:demo", from_state="qualifying", to_state="hot",
                        signal="interested", ts=NOW - DAY)
    s.add_card(msg_id=101, contact_id="101:demo", kind="escalation", ts=NOW - DAY)
    s.record_payment(PaymentRecord(
        contact_id="102:demo", dedup_key=make_dedup_key("tap", 5), ts=NOW - DAY,
        confirmed_by="owner", amount=from_major("750", "USD")))
    s.record_payment(PaymentRecord(
        contact_id="101:demo", dedup_key=make_dedup_key("tap", 6), ts=NOW - DAY,
        confirmed_by="owner", amount=None))
    del s
    return str(p)


def test_dialogs_counted_by_inbound_activity(db):
    out = M.summary(db, now=NOW, period="week")
    assert out["funnel"]["dialogs"] == 3


def test_qualified_comes_from_transitions(db):
    out = M.summary(db, now=NOW, period="week")
    assert out["funnel"]["qualified"] == 1


def test_avg_check_ignores_payments_without_amount(db):
    out = M.summary(db, now=NOW, period="week")
    assert out["avg_check"] == 750.0
    assert out["avg_check_basis"] == (1, 2), "подпись «за N з M оплат» врёт"


def test_payments_count_includes_ones_without_amount(db):
    out = M.summary(db, now=NOW, period="week")
    assert out["funnel"]["payments"] == 2


def test_empty_period_gives_none_not_zero(db):
    """Ноль читается как «было и упало», пустота — как «не накопилось».
    Путать нельзя (спека §6.3)."""
    s = M.series_for(db, ["avg_check"], "day", now=NOW - 30 * DAY)[0]
    assert s.total is None


def test_series_bucket_count_matches_period(db):
    day = M.series_for(db, ["dialogs"], "day", now=NOW)[0]
    week = M.series_for(db, ["dialogs"], "week", now=NOW)[0]
    assert len(day.points) == 24
    assert len(week.points) == 7


def test_metric_without_table_is_marked_unavailable(tmp_path):
    """Старая база без наших таблиц: метрика обязана честно сказать
    «історія накопичується», а не показать 0."""
    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, contact_id TEXT, role TEXT,"
        " text TEXT, ts REAL);"
        "CREATE TABLE contacts (contact_id TEXT, state TEXT, paused INTEGER);"
        "CREATE TABLE console_cards (msg_id INTEGER, contact_id TEXT, kind TEXT, ts REAL);"
        "CREATE TABLE runtime_flags (key TEXT, value TEXT, ts REAL);")
    conn.commit()
    conn.close()

    s = M.series_for(str(p), ["payments"], "week", now=NOW)[0]
    assert s.available is False
    assert s.history_since is None


def test_history_since_is_reported_once_data_exists(db):
    s = M.series_for(db, ["payments"], "week", now=NOW)[0]
    assert s.available is True
    assert s.history_since == pytest.approx(NOW - DAY)


def test_metrics_connection_is_read_only(db):
    """Дашборд физически не может писать в боевую БД клиента."""
    conn = M._ro(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO contacts (contact_id, state) VALUES ('x','new')")
    conn.close()


def test_duration_uses_median_not_mean(tmp_path):
    p = tmp_path / "m.db"
    s = Store(str(p))
    for i, span in enumerate([1 * DAY, 1 * DAY, 1 * DAY, 100 * DAY]):
        cid = f"{i}:demo"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "a", ts=NOW - span - 10)
        s.add_message(cid, "user", "b", ts=NOW - 10)
    del s
    out = M.series_for(str(p), ["duration"], "week", now=NOW)[0]
    # среднее было бы ~25.75 дн; медиана держится около суток
    assert out.total is not None and out.total < 5


def test_needs_attention_uses_the_same_flag_as_the_pult(db):
    s = Store(db)
    s.set_runtime_flag("esc_active:101:demo", "bot:1:5", ts=NOW)
    s.set_runtime_flag("esc_active:100:demo", "", ts=NOW)   # закрыта владельцем
    del s

    items = M.needs_attention(db, now=NOW)
    ids = [i["contact_id"] for i in items]
    assert "101:demo" in ids
    assert "100:demo" not in ids, "закрытая карточка не имеет права висеть в блоке"
