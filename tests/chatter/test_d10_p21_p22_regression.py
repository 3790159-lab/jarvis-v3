"""P21/P22: красное ДО фикса, воспроизводимо и без живых денег.

Зачем этот файл существует. Спека Д-10
(`docs/superpowers/specs/2026-08-06-d10-clean-contact-scenario.md`, §Порядок
работ) требует сначала получить КРАСНОЕ на живом прогоне и только потом
править промпт: зелёный сразу после правки ничего не доказывает, потому что
не показывает, что тест вообще способен покраснеть.

Живой дрил этого дать не смог. Прогоны 07.08 и 08.08 на неизменном промпте
(последняя правка `classifier.py` — 26.07, `cfad3f13`) прошли по шагам 3/4/6
ЗЕЛЁНЫМИ, и зелёное было настоящим, а не вакуумным: таймстемпы слота 08.08
показывают `brief` закрытым ходом 1 и не переоткрытым на ходе 3, а `recalc`
рождённым ровно на своём шаге (10:05:39 против реплики 10:05:34). То есть
P21/P22 НЕДЕТЕРМИНИРОВАНЫ — воспроизвелись 06.08 и с тех пор нет. Ждать
случайного красного значит платить $0.30 за прогон, который с большей
вероятностью не скажет ничего.

Что фиксирует тест. Записанный ответ классификатора 06.08 (обе находки
дословно из `docs/chatter/PROBLEMS.md`) прогоняется через НАСТОЯЩИЙ
`merge_obligations` и НАСТОЯЩИЕ проверки дрила, взятые из ЖИВОГО yaml
сценария. Красное до фикса становится воспроизводимым и бесплатным.

🔴 Границы. Тест НЕ проверяет модель — поведение модели офлайн недоступно.
Он держит ПУТЬ ОБНАРУЖЕНИЯ: если ослабнет разбор ответа классификатора,
слияние слота или сами проверки сценария, дефект перестанет быть видимым —
и тогда покраснеет этот файл. Живой дрил остаётся единственным судьёй того,
как ведёт себя модель.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.core.drill import Facts, check_step, parse_scenario
from chatter.core.obligations_slot import merge_obligations

_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = _ROOT / "docs" / "chatter" / "drills" / "d10-obligations.yaml"

NOW = 1786017326.0        # 06.08 14:55 — время того самого прогона
PROFILE = "створення логотипу з нуля (не ребрендинг), сфера — кав'ярня"

# Шаги сценария, заведённые под эти находки (спека §Сценарий, шаги 3/4/6).
# Индексы, а не тексты: сверка с живым yaml — смысл теста.
STEP_P21, STEP_P22, STEP_SUMMARY = 2, 3, 5


@pytest.fixture(scope="module")
def scenario():
    return parse_scenario(SCENARIO.read_text(encoding="utf-8"))


def _slot_after(existing, updates):
    """{okey: status} после хода — ровно то, что читает судья дрила из БД."""
    merged = merge_obligations(existing, updates, now=NOW, current_msg_id=431)
    return merged, {o.okey: o.status for o in merged}


def _facts(slot, before, *, cards=0):
    return Facts(cache="hit", obligations=slot, obligations_before=before,
                 profile=PROFILE, classifier_errors=0, cards_delivered=cards,
                 replies=1, process_ends=1,
                 obligations_bot=slot, obligations_bot_before=before)


def _obligations_check(step, facts):
    checks = check_step(step.expect, facts)
    # Красное обязано прийти ИМЕННО от проверки слота. Дефект, замаскированный
    # сбоем кэша или классификатора, — другой дефект, и путать их дорого.
    others = [c for c in checks if c.key != "obligations" and not c.ok]
    assert not others, f"покраснело не то: {[(c.key, c.detail) for c in others]}"
    return next(c for c in checks if c.key == "obligations")


# Слот на входе в ход 3: brief закрыт ходом 1 — так было и 06.08
# («ход 1 закрыл brief ПРАВИЛЬНО», PROBLEMS.md P21).
BRIEF_CLOSED = [{"kind": "brief", "owed_by": "bot", "status": "delivered",
                 "detail": "уточнюючі питання задані"}]


@pytest.fixture
def brief_delivered():
    merged, slot = _slot_after([], BRIEF_CLOSED)
    assert slot == {"brief": "delivered"}, "предпосылка теста не собралась"
    return merged, slot


# --- P21: brief переоткрыт в ожидании материалов клиента ---------------------

def test_p21_reopened_brief_turns_step_red(scenario, brief_delivered):
    """Дословная находка 06.08: классификатор вернул brief в open с detail
    «чекаємо відповідь клієнта…», хотя промпт запрещает это (classifier.py:194-199)."""
    existing, before = brief_delivered
    _, slot = _slot_after(existing, [
        {"kind": "brief", "owed_by": "bot", "status": "open",
         "detail": "чекаємо відповідь клієнта про сферу бізнесу і напрацювання"},
    ])
    assert slot == {"brief": "open"}, "слияние не воспроизвело переоткрытие"

    check = _obligations_check(scenario.steps[STEP_P21], _facts(slot, before))
    assert not check.ok, "шаг-сторож P21 не покраснел на своём же дефекте"
    assert "brief" in check.detail and "open" in check.detail


def test_p21_step_is_green_when_brief_stays_closed(scenario, brief_delivered):
    """Обратная сторона: на правильном поведении шаг ЗЕЛЁНЫЙ. Без этого тест
    доказывал бы лишь то, что проверка умеет краснеть всегда."""
    existing, before = brief_delivered
    _, slot = _slot_after(existing, [])          # ход ничего не менял в слоте
    check = _obligations_check(scenario.steps[STEP_P21], _facts(slot, before))
    assert check.ok, f"ложное красное на исправном ходе: {check.detail}"


# --- P22: пересчёт цены уехал в свободную корзину other ----------------------

def test_p22_recalc_in_other_bucket_turns_step_red(scenario, brief_delivered):
    """06.08 запрос на пересчёт лёг как `other:уточнити у керівниці цін`
    (kind=other) вместо канонического `recalc`."""
    existing, before = brief_delivered
    _, slot = _slot_after(existing, [
        {"kind": "other", "owed_by": "bot", "status": "open",
         "detail": "уточнити у керівниці цін"},
    ])
    assert "recalc" not in slot, "фикстура не воспроизвела уход в other"
    assert any(k.startswith("other:") for k in slot), slot

    check = _obligations_check(scenario.steps[STEP_P22], _facts(slot, before))
    assert not check.ok, "шаг-сторож P22 не покраснел на своём же дефекте"
    assert "recalc" in check.detail


def test_p22_step_is_green_when_recalc_is_canonical(scenario, brief_delivered):
    existing, before = brief_delivered
    _, slot = _slot_after(existing, [
        {"kind": "recalc", "owed_by": "bot", "status": "open",
         "detail": "запит на прорахунок 3 варіантів зафіксовано"},
    ])
    check = _obligations_check(scenario.steps[STEP_P22], _facts(slot, before))
    assert check.ok, f"ложное красное на каноничном recalc: {check.detail}"


# --- сводный шаг 6: ловит оба дефекта разом ---------------------------------

def test_summary_step_reports_both_defects(scenario, brief_delivered):
    """Шаг 6 — не дубль шагов 3/4: он единственный, кто видит слот целиком,
    и обязан назвать ОБЕ находки, а не первую попавшуюся."""
    existing, before = brief_delivered
    _, slot = _slot_after(existing, [
        {"kind": "brief", "owed_by": "bot", "status": "open",
         "detail": "чекаємо відповідь клієнта"},
        {"kind": "other", "owed_by": "bot", "status": "open",
         "detail": "уточнити у керівниці цін"},
        {"kind": "owner_write", "owed_by": "bot", "status": "delivered",
         "detail": "картка керівниці доставлена"},
    ])
    check = _obligations_check(scenario.steps[STEP_SUMMARY],
                               _facts(slot, before, cards=1))
    assert not check.ok
    assert "recalc" in check.detail and "brief" in check.detail


# --- сторож самой привязки к сценарию ---------------------------------------

def test_guard_steps_still_check_the_obligations_they_were_added_for(scenario):
    """DEV-19 по смыслу: тест держится за ИНДЕКСЫ шагов живого yaml. Если
    сценарий перепишут и шаги-сторожа уедут или потеряют проверку слота, этот
    файл продолжил бы «доказывать» красное на чужом шаге. Сверяем явно."""
    assert len(scenario.steps) == 6, "сценарий переписан — проверить индексы ниже"
    assert "brief" in (scenario.steps[STEP_P21].expect.get("obligations") or {}), \
        "шаг 3 больше не сторожит brief (P21)"
    assert "recalc" in (scenario.steps[STEP_P22].expect.get("obligations") or {}), \
        "шаг 4 больше не сторожит recalc (P22)"
    summary = scenario.steps[STEP_SUMMARY].expect.get("obligations") or {}
    assert {"brief", "recalc"} <= set(summary), "шаг 6 перестал быть сводным"
