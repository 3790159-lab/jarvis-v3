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


@pytest.mark.parametrize("reply", [
    "Хочу нанести лёгкий макияж, займёт 5 минут.",
    "У меня есть маленький список из 3 пунктов.",
])
def test_ma_substring_words_do_not_false_positive_as_may_deadline(reply):
    # Regression: the guardrail's "ма" stem (for май/March-May) used to be a
    # dangerously short 2-char substring that matched unrelated words like
    # "макияж"/"маленький", falsely tripping the bare-deadline-unit check.
    assert contains_unbacked_claim(reply, KNOWLEDGE) is False


# --- M1: подтверждение числа КОНТЕКСТНОЕ, не «где-то в файле» ------------------
# Реальные значения демо-базы (цены и срок), обеспеченные СВОИМ контекстом.
DEMO_KB = (
    "Консультация онлайн 60 минут. Цена: 5000 грн. "
    "Фотосессия 15000 грн за съёмку (1.5–2 часа, 15 обработанных кадров). "
    "Студия +2000 грн. Предоплата 50% для брони. "
    "Перенос возможен не позднее чем за 48 часов. Готовые фото в течение 7 дней."
)


@pytest.mark.parametrize("reply", [
    "Консультация 5000 грн.",
    "Съёмка 15000 грн.",
    "Студия +2000 грн.",
    "Предоплата 50%.",
    "Перенос возможен за 48 часов.",
])
def test_demo_backed_values_stay_backed(reply):
    # СТОП-ГАРД: контекстное подтверждение НЕ должно душить реальные обеспеченные
    # ответы демо (цена в ценовом контексте, срок 48ч в срочном).
    assert contains_unbacked_claim(reply, DEMO_KB) is False


def test_price_number_reused_as_deadline_is_flagged():
    # 5000 обеспечено как ЦЕНА; переиспользованное как СРОК — не обеспечено.
    assert contains_unbacked_claim("Приедем через 5000 дней", DEMO_KB) is True


# --- Б1: цена и срок в ОДНОМ предложении ------------------------------------
# Баг с живого теста volska 2026-07-21: предложение с ценовым контекстом
# требовало, чтобы ВСЕ его числа лежали в ЦЕНОВОМ множестве. Срочные числа
# («5–10 робочих днів») ценовыми не являются → обеспеченный ответ подавлялся,
# лид вместо прайса получал «уточню детали». Это ровно то, за чем лид приходит.
#
# Проблема НЕ украиноязычная и НЕ волска-специфичная: у Ани она маскировалась
# тем, что её knowledge сливает цену и время в один фрагмент («15000 грн за
# съёмку (1.5–2 часа)»), и он кормит ОБА множества. Стоит разнести цены и сроки
# по разным строкам — и падает так же. Поэтому стоп-гард идёт на ДЕМО-базе.

# Цены и сроки в РАЗНЫХ фрагментах — множества не пересекаются (структура volska).
SPLIT_KB = (
    "Створення логотипа — 300–400 $. Повна айдентика — 700–900 $.\n"
    "Створення логотипа — 5–10 робочих днів. Створення айдентики — 10–15 робочих днів."
)


@pytest.mark.parametrize("reply", [
    # Аня: цена 15000 обеспечена ценой, срок 7 дней обеспечен сроком — в одном предложении.
    "Съёмка 15000 грн, готовые фото в течение 7 дней.",
    "Консультация 5000 грн, перенос возможен за 48 часов.",
])
def test_backed_price_and_deadline_in_one_sentence_not_flagged_demo(reply):
    assert contains_unbacked_claim(reply, DEMO_KB) is False


@pytest.mark.parametrize("reply", [
    "Створення логотипа окремо — 300–400 $, термін виконання 5–10 робочих днів.",
    "Повна айдентика — 700–900 $, це вже 10–15 робочих днів.",
])
def test_backed_price_and_deadline_in_one_sentence_not_flagged_split_kb(reply):
    assert contains_unbacked_claim(reply, SPLIT_KB) is False


def test_deadline_idiom_with_za_is_not_read_as_price():
    # «за N <единица времени>» ловится _PRICE_CONTEXT как ценовой маркер (\bза\s+\d),
    # из-за чего срочное число проверялось против цен. Обеспеченный срок — не цена.
    assert contains_unbacked_claim("Зробимо за 5 робочих днів.", SPLIT_KB) is False


def test_invented_deadline_inside_price_sentence_still_flagged():
    # СТОП-ГАРД: послабление не должно пускать выдуманный СРОК в ценовом предложении.
    assert contains_unbacked_claim(
        "Створення логотипа — 300–400 $, зробимо за 99 робочих днів.", SPLIT_KB) is True
    assert contains_unbacked_claim(
        "Съёмка 15000 грн, готовые фото за 99 дней.", DEMO_KB) is True


def test_invented_price_inside_price_and_deadline_sentence_still_flagged():
    # СТОП-ГАРД: выдуманная ЦЕНА рядом с обеспеченным сроком по-прежнему флагается.
    assert contains_unbacked_claim(
        "Створення логотипа — 77777 $, термін 5–10 робочих днів.", SPLIT_KB) is True
    assert contains_unbacked_claim(
        "Съёмка 77777 грн, готовые фото в течение 7 дней.", DEMO_KB) is True


def test_count_number_reused_as_deadline_is_flagged():
    # 15 обеспечено как КОЛИЧЕСТВО кадров (не срок, не цена). Выдуманный срок,
    # переиспользующий 15, теперь флагается (раньше плоское known_numbers пускало).
    kb = "В съёмку входит 15 обработанных кадров. Готово за 7 дней."
    assert contains_unbacked_claim("Перенесём за 15 часов", kb) is True
    assert contains_unbacked_claim("Готово за 7 дней", kb) is False   # реальный срок обеспечен


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
