from __future__ import annotations
import pytest
from chatter.core.guardrails import contains_unbacked_claim, within_hourly_limit, within_daily_cap
from chatter.storage.db import Store

KNOWLEDGE = (
    "Консультация 5000 руб. Фотосессия 15000 руб. Студия +2000 руб. "
    "Предоплата 50%. Готовые фото в течение 7 дней. Работаю пн-сб."
)

MUST_FLAG = [
    "Могу сделать за 3000 руб, специально для вас.",
    "Это будет 3000.",
    "Доставим за 3 дня.",
    "Готово через неделю.",
    "Сделаю к пятнице.",
    "Дам скидку 20%.",
]

MUST_NOT_FLAG = [
    "Пришлите 1-2 фото для примера.",
    "Есть 2 формата съёмки.",
    "Займёт минут 5, расскажите подробнее?",
    "Консультация стоит 5000 руб.",
    "Предоплата 50%.",
    "Готовые фото в течение 7 дней.",
    "Расскажите, что именно хотите?",
]


@pytest.mark.parametrize("reply", MUST_FLAG)
def test_flags_unbacked_price_or_deadline_claims(reply):
    assert contains_unbacked_claim(reply, KNOWLEDGE) is True


@pytest.mark.parametrize("reply", MUST_NOT_FLAG)
def test_does_not_flag_backed_or_innocent_replies(reply):
    assert contains_unbacked_claim(reply, KNOWLEDGE) is False


def test_hourly_limit(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    for t in range(3):
        s.add_message("u1", "assistant", "x", ts=1000.0 + t)
    # limit 3, window 3600s, now=1002 -> already 3 in last hour -> not allowed
    assert within_hourly_limit(s, "u1", now=1002.0, limit=3) is False
    assert within_hourly_limit(s, "u1", now=1002.0, limit=5) is True


def test_daily_cap(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    for t in range(2):
        s.add_message("u1", "assistant", "x", ts=100.0 + t)
    assert within_daily_cap(s, now=200.0, cap=2) is False
    assert within_daily_cap(s, now=200.0, cap=3) is True
