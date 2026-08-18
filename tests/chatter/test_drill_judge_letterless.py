# -*- coding: utf-8 -*-
"""DEV-32: судья дрила ВРЁТ на любой реплике без букв.

Сторожа написаны ОТ ТИКЕТА (`state/dev_backlog_pipeline.md`, DEV-32, варианты
2 + 3), до правки `chatter/core/drill.py`.

## Класс ошибки

`match_step` опознаёт шаг по ТЕКСТУ: `_normalize_say` выбрасывает всё, кроме
букв, цифр и пробелов. Для «🔥👍» нормализация даёт ПУСТУЮ строку, `match_step`
возвращает `None`, ход не опознаётся, и судья пишет красное `bench` при
идеально работающем боте. Живьём 17.08 (дрил 6 онбординга Ярины): обе
содержательные проверки шага зелёные, прогон вышел с кодом 1, и красное
относилось К СУДЬЕ, а не к Ярине.

Красным становится ЛЮБАЯ реплика без букв: эмодзи, «+1», «...», голое число.
Это не экзотика, а нормальный ход живого лида — и дрил «реакция на
бессодержательное сообщение» без него не написать.

## Почему это дороже, чем выглядит

Контракт харнесса — **код выхода = вердикт**. Сигнал, всегда красный при
законном сценарии, перестаёт быть сторожем и становится фоном: вердикт
приучаются читать как «ну там опять стенд», и однажды так же прочитают
настоящее красное. Тем же силуэтом болел гейт DEV-31.

## Что закрывается

* **вариант 2** — нормализованный текст пуст ⇒ сравниваем СЫРЫЕ строки тем же
  порогом. Общий случай, а не только «эмодзи против эмодзи»;
* **вариант 3** — `parse_scenario` не принимает сценарий, чей шаг не опознаёт
  сам себя. Тогда сценарий, обречённый покраснеть, нельзя написать вовсе —
  отказ громкий и ДО старта, а не красное задним числом.

🔴 Инвариант, который правка не имеет права ослабить: посторонняя реплика не
притворяется шагом. Запасной путь по сырому тексту сравнивает те же строки тем
же порогом — «👍» и «...» обязаны остаться разными репликами.

Замер перед правкой (все три живых сценария `docs/chatter/drills/`):
неопознаваемых шагов сейчас — 1 (эмодзи-шаг Ярины), с сырым запасным — 0.
То есть вариант 3 не отвергает ни одного существующего сценария.

$0: только чистые функции и yaml. В сеть и в БД тесты не ходят.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.core.drill import (
    DrillScenarioError, Step, _normalize_say, match_step, parse_scenario,
)

REPO = Path(__file__).resolve().parents[2]
LIVE_DRILLS = REPO / "docs" / "chatter" / "drills"


def _steps(*says) -> tuple:
    return tuple(Step(say=s, expect={"classifier_errors": 0}) for s in says)


# ── реплика без букв опознаётся ──────────────────────────────────────────────

def test_an_emoji_only_step_is_recognised_as_itself():
    """Живой шаг 6 Ярины. Сегодня здесь `None`, и шаг красный при исправном боте."""
    steps = _steps("Привіт! Скільки коштує детейлінг-мийка?", "🔥👍")
    assert _normalize_say("🔥👍") == "", "фикстура протухла: нормализация вдруг не пуста"
    assert match_step("🔥👍", steps) == 1, (
        "эмодзи-реплика не опознана — шаг обречён краснеть при здоровом боте")


@pytest.mark.parametrize("say", ["👍", "+1", "...", "!!!", "🔥🔥🔥"])
def test_letterless_replies_of_every_kind_are_recognised(say):
    """Класс, а не один случай: эмодзи, «+1», многоточие, голые знаки."""
    steps = _steps("Скільки це коштує?", say)
    assert match_step(say, steps) == 1, f"«{say}» не опознан — красное от судьи"


def test_a_letterless_reply_matches_the_right_step_among_several():
    """Двух бесбуквенных шагов в сценарии достаточно, чтобы «совпало хоть с
    чем-то» перестало быть ответом: реплика обязана найти ИМЕННО СВОЙ шаг."""
    steps = _steps("Доброго дня!", "👍", "🔥🔥🔥")
    assert match_step("🔥🔥🔥", steps) == 2
    assert match_step("👍", steps) == 1


# ── инвариант не ослаблен ────────────────────────────────────────────────────

def test_a_stranger_reply_still_does_not_pretend_to_be_a_step():
    """Посторонняя фраза обязана остаться неопознанной: молчаливое
    «притворилась шагом» разъезжает стенд на шаг (прогон №5, 26.07)."""
    steps = _steps("Скільки коштує детейлінг-мийка?", "Дякую, чекаю")
    assert match_step("Де ви знаходитесь і як до вас доїхати?", steps) is None


def test_two_different_letterless_replies_do_not_collapse_into_one():
    """🔴 ГЛАВНЫЙ сторож правки. Если запасной путь начнёт считать все
    бесбуквенные реплики одинаковыми, судья снова припишет проверки чужому
    ходу — только теперь молча и с зелёным видом."""
    steps = _steps("Доброго дня!", "👍")
    assert match_step("...", steps) is None, (
        "«...» опознано как шаг «👍» — все реплики без букв слиплись в одну")


def test_a_typo_is_still_forgiven_on_ordinary_replies():
    """Порог не тронут: одна опечатка не повод рвать прогон."""
    steps = _steps("А що саме входить у вартість?", "Дякую, чекаю")
    assert match_step("Дякую, чекаю!!!", steps) == 1


def test_an_empty_reply_matches_nothing():
    """Пустая строка и пробелы — не реплика вовсе."""
    steps = _steps("Доброго дня!", "👍")
    assert match_step("", steps) is None
    assert match_step("   ", steps) is None


# ── вариант 3: сценарий, обречённый краснеть, не принимается ─────────────────

def _yaml(*says) -> str:
    body = "\n".join(f'  - say: "{s}"\n    expect:\n      classifier_errors: 0'
                     for s in says)
    return f'name: тест\nclient: yarina\ncontact: "8849893367:yarina"\nsteps:\n{body}\n'


def test_a_scenario_whose_step_cannot_identify_itself_is_refused():
    """Два почти одинаковых шага: реплика второго опознаётся как первый, и
    проверки уедут чужому ходу. Отказ обязан прийти ДО старта, а не красным
    задним числом — платный прогон уже состоится."""
    with pytest.raises(DrillScenarioError) as exc:
        parse_scenario(_yaml("Скільки коштує мийка?", "Скільки коштує мийка??"))
    assert "2" in str(exc.value), f"отказ не назвал номер шага: {exc.value}"


def test_the_refusal_names_both_steps():
    """Человеку чинить сценарий: одного «не принят» мало."""
    with pytest.raises(DrillScenarioError) as exc:
        parse_scenario(_yaml("Доброго дня, скільки це коштує?",
                             "Доброго дня, скільки це коштує!"))
    msg = str(exc.value)
    assert "шаг" in msg.casefold() and "1" in msg and "2" in msg, msg


def test_a_healthy_scenario_is_still_accepted():
    """Обратная сторона: правило не имеет права запрещать нормальный сценарий."""
    scenario = parse_scenario(_yaml("Скільки коштує детейлінг-мийка?",
                                    "А ти бот чи жива людина?", "🔥👍"))
    assert len(scenario.steps) == 3


@pytest.mark.parametrize("path", sorted(LIVE_DRILLS.glob("*.yaml")),
                         ids=lambda p: p.name)
def test_every_live_scenario_survives_the_new_refusal(path):
    """Замер, закреплённый сторожем: ни один ЖИВОЙ сценарий репозитория не
    отвергается новым правилом. Без этого вариант 3 чинил бы судью ценой
    остановленного стенда."""
    scenario = parse_scenario(path.read_text(encoding="utf-8"))
    for i, step in enumerate(scenario.steps):
        assert match_step(step.say, scenario.steps) == i, (
            f"{path.name}: шаг {i + 1} не опознаёт сам себя")
