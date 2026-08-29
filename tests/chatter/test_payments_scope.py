"""Тексты объёма на ступенях торга: заглушки — только дрилу (решение владельца
2026-08-10).

Реальные формулировки придут от Ольги. До тех пор ступени несут ЗАГЛУШКИ с
явной пометкой `placeholder: true`, и конфиг с заглушками не имеет права
обслуживать живой контакт: уступка, обменянная на текст «TODO уточнить»,
— это скидка без встречного сокращения (риск 10.12) плюс заглушка в диалоге
живого лида.

Пометка ЯВНАЯ и только явная: строка, случайно похожая на TODO, заглушкой не
считается, иначе валидатор начнёт угадывать.
"""
from __future__ import annotations

import pytest

from chatter.payments.drill_gate import NotForProduction
from chatter.payments.pricing import load_pricing
from chatter.payments.scope import (
    ScopeConfigError, assert_pricing_usable, load_scope_texts, resolve_scope_text,
)

LIVE = "telegram:555000111:volska"
DRILL = "telegram:8849893367:volska"

KNOWLEDGE = "- Створення логотипа — 300–400 $\n"

PRICING = load_pricing({
    "amount_source": "price_upper",
    "positions": {"logo_create": {
        "title": "Логотип", "currency": "USD", "price_range": [300, 400],
        "ladder": [{"amount": 400, "scope_key": "logo_full"},
                   {"amount": 350, "scope_key": "logo_s350"},
                   {"amount": 300, "scope_key": "logo_floor"}]}},
}, knowledge=KNOWLEDGE)

REAL = load_scope_texts({
    "logo_full": {"text": "3 концепції, 3 кола правок"},
    "logo_s350": {"text": "2 концепції, 2 кола правок"},
    "logo_floor": {"text": "1 концепція, 1 коло правок"},
})

WITH_STUBS = load_scope_texts({
    "logo_full": {"text": "3 концепції, 3 кола правок"},
    "logo_s350": {"text": "TODO: узгодити з Ольгою", "placeholder": True},
    "logo_floor": {"text": "TODO: узгодити з Ольгою", "placeholder": True},
})


def test_real_text_serves_anyone():
    assert resolve_scope_text("logo_full", REAL, contact_id=LIVE) == "3 концепції, 3 кола правок"
    assert resolve_scope_text("logo_full", WITH_STUBS, contact_id=LIVE)


def test_placeholder_serves_drill_contact():
    assert resolve_scope_text("logo_s350", WITH_STUBS, contact_id=DRILL).startswith("TODO")


def test_placeholder_refuses_live_contact():
    """Тот же гейт, что у test-реквизитов: тестовый актив живому не уходит."""
    with pytest.raises(NotForProduction, match="logo_s350"):
        resolve_scope_text("logo_s350", WITH_STUBS, contact_id=LIVE)


def test_marker_must_be_explicit_not_guessed():
    """Строка, похожая на заглушку, заглушкой НЕ считается: угадывание сделало
    бы валидатор непредсказуемым в обе стороны."""
    texts = load_scope_texts({"logo_full": {"text": "TODO: щось"}})
    assert resolve_scope_text("logo_full", texts, contact_id=LIVE) == "TODO: щось"


def test_unknown_scope_key_is_an_error_not_an_empty_string():
    with pytest.raises(ScopeConfigError, match="logo_s999"):
        resolve_scope_text("logo_s999", REAL, contact_id=DRILL)


def test_empty_text_is_a_config_error():
    with pytest.raises(ScopeConfigError):
        load_scope_texts({"logo_full": {"text": "  "}})


# ── проверка конфига целиком, до разговора ─────────────────────────────────

def test_full_config_with_stubs_fails_validation_for_a_live_contact():
    """Решение владельца: «конфиг с заглушками не должен проходить валидацию
    для не-дрил контактов». Проверяем ДО разговора, а не в момент уступки."""
    with pytest.raises(NotForProduction) as exc:
        assert_pricing_usable(PRICING, WITH_STUBS, contact_id=LIVE)
    msg = str(exc.value)
    assert "logo_s350" in msg and "logo_floor" in msg, "названы должны быть ВСЕ заглушки"


def test_full_config_with_stubs_passes_for_a_drill_contact():
    assert_pricing_usable(PRICING, WITH_STUBS, contact_id=DRILL)


def test_full_real_config_passes_for_everyone():
    assert_pricing_usable(PRICING, REAL, contact_id=LIVE)
    assert_pricing_usable(PRICING, REAL, contact_id=DRILL)


def test_missing_text_for_a_declared_step_is_caught_by_the_same_check():
    """Ступень без текста объёма — это «молча дешевле» в чистом виде."""
    partial = load_scope_texts({"logo_full": {"text": "3 концепції"}})
    with pytest.raises(ScopeConfigError, match="logo_s350"):
        assert_pricing_usable(PRICING, partial, contact_id=DRILL)
