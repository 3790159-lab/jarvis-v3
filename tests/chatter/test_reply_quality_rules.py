# -*- coding: utf-8 -*-
"""Четыре правила ответа (спека `docs/superpowers/specs/2026-08-17-reply-
quality-four-rules.md`), сторожа §5.

Сторожа написаны ОТ СПЕКИ. Поведение LLM тестами не ловится — ловится СБОРКА
и детерминированные проверки вокруг неё, и ровно это здесь и сторожится:

  * правило доехало до системного слоя КАЖДОГО клиента (S1);
  * формулировка живёт в ОДНОМ месте, а не копией в промпте и копией в
    playbook (S2) — иначе через месяц они разъедутся, и никто не заметит,
    какая из двух настоящая ([[jarvis-two-numbers-for-one-thing]]);
  * обороты претензии ловятся ДЕТЕРМИНИРОВАННО, как `forbidden_terms`, а не
    уговором в промпте (S3, S4), и нейтральная передача при этом проходит
    (S5) — сигнал, красный при законной работе, это фон, а не сторож;
  * число из ИСТОРИИ диалога не становится обеспеченным (S7).

S6 (счётчик вопросов) НЕ реализован: правило 3 отложено владельцем 17.08
(§3 спеки). Сторожа на него здесь нет намеренно — тест на невыполненное
решение краснел бы как дефект.

**Почему S7 бьёт по проводке, а не по функции.** `deterministic_escalation`
истории не принимает вовсе, и тест на неё доказал бы только сигнатуру.
Дорогая ошибка живёт СЛОЕМ ВЫШЕ: кто-нибудь «улучшит» сборку и подмешает
историю к `knowledge` в вызове — множество обеспеченных чисел вырастет, и
выдуманная в прошлой реплике цифра станет своей.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from chatter.config.loader import load_config
from chatter.core import escalation as escalation_module
from chatter.core import reply_rules
from chatter.core.brain import Brain, build_system_prompt
from chatter.core.escalation import deterministic_escalation
from chatter.core.llm import FakeLLM
from chatter.run import Deps, _escalation_pass
from chatter.storage.db import Store

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"
CHATTER_SRC = Path(__file__).resolve().parents[2] / "chatter"

# Каталоги, которые обязаны нести правило. `demo` — эталон сборки, `volska` и
# `yarina` — ЖИВЫЕ клиенты, ради которых спека и писалась (§0).
LIVE_SLUGS = ("demo", "volska", "yarina")


def _cfg(slug: str):
    return load_config(CLIENTS, slug)


# ═════════════════════════════════════════════════════════════════════════════
# S1. Правило границы допущений — в системном слое КАЖДОГО клиента
#
# §1: допущение о МАРШРУТЕ законно, допущение об УСЛУГЕ запрещено. Критерий
# владельца — не «есть ли в брифе», а «может ли навредить»: маршрутное ведёт к
# человеку, тот поправит; допущение об услуге доезжает до клиента, он приедет
# с ожиданием подбора, которого может не быть.
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("slug", LIVE_SLUGS)
def test_assumption_boundary_rule_reaches_every_clients_system_prompt(slug):
    """S1 спеки. Мутация: убрать строку из сборки.

    Проверяется КАЖДЫЙ живой клиент, а не один демо-каталог: правило,
    доехавшее до эталона и не доехавшее до Ольги, выглядит внедрённым.
    """
    try:
        cfg = _cfg(slug)
    except Exception as exc:                       # pragma: no cover — среда
        pytest.skip(f"каталог {slug} не читается в этой среде: {exc}")
    sp = build_system_prompt(cfg)
    assert reply_rules.ASSUMPTION_BOUNDARY_UK in sp, (
        f"{slug}: правила границы допущений нет в системном слое — бот вправе "
        f"домыслить услугу, и лид приедет с ожиданием, которого никто не давал")


def test_the_rule_names_both_halves_of_the_boundary():
    """Ловит: правило, запрещающее ВСЁ. Половина без второй половины — фон.

    Запрет домысливать без разрешения на маршрутную реплику превращает бота в
    молчуна: он не сможет сказать даже «це до старшого майстра», а это ровно
    то, что он обязан говорить.
    """
    rule = reply_rules.ASSUMPTION_BOUNDARY_UK.casefold()
    assert "маршрут" in rule or "передат" in rule or "переда" in rule, (
        f"правило не называет РАЗРЕШЁННУЮ половину (маршрут к человеку): {rule!r}")
    assert "послуг" in rule or "услуг" in rule, (
        f"правило не называет ЗАПРЕЩЁННУЮ половину (домысел об услуге): {rule!r}")


# ═════════════════════════════════════════════════════════════════════════════
# S2. Один источник формулировки
#
# §1: «Формулировка одна на оба места». Мутация — развести на две копии.
# Две копии одного правила — это [[jarvis-two-numbers-for-one-thing]]: они
# расходятся молча, и в споре «что считать правильным» побеждает та, которую
# нашли первой.
# ═════════════════════════════════════════════════════════════════════════════

def _sources_mentioning(fragment: str) -> list[str]:
    """Файлы `chatter/**/*.py`, где встречается ДОСЛОВНЫЙ фрагмент правила."""
    hits = []
    for path in sorted(CHATTER_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:                            # pragma: no cover — среда
            continue
        if fragment in text:
            # `as_posix`, а не `str`: на Windows разделитель обратный, и сторож
            # падал бы на самом себе, а не на копии правила.
            hits.append(path.relative_to(CHATTER_SRC).as_posix())
    return hits


@pytest.mark.parametrize("attr", ["ASSUMPTION_BOUNDARY_UK", "HISTORY_IS_NOT_A_SOURCE_UK"])
def test_each_rule_text_lives_in_exactly_one_source_file(attr):
    """S2 спеки. Мутация: развести формулировку на две копии.

    Сторож стоит НА РАЗМЕТКЕ, а не на поведении: копию видно только так.
    Берётся длинный дословный кусок правила — короткий совпал бы случайно.
    """
    text = getattr(reply_rules, attr)
    fragment = text[:60]
    hits = _sources_mentioning(fragment)
    assert hits == ["core/reply_rules.py"], (
        f"{attr}: формулировка встречается в {hits} — копия правила живёт "
        f"своей жизнью и разъедется с оригиналом молча; правило обязано "
        f"импортироваться из `core/reply_rules.py`, а не переписываться")


@pytest.mark.parametrize("slug", ["volska", "yarina"])
def test_every_live_clients_playbook_carries_the_rules(slug):
    """Вторая половина §1: правило живёт и в playbook, не только в промпте.

    Playbook — это то, что читает ЧЕЛОВЕК, когда правит конфиг. Правило,
    существующее только в коде сборки промпта, для него невидимо: он перепишет
    playbook и не узнает, что нарушил договорённость. Оба живых каталога
    правлены руками, поэтому сторож нужен именно на них.
    """
    path = CLIENTS / slug / "playbook.md"
    if not path.exists():                          # pragma: no cover — среда
        pytest.skip(f"каталога {slug} нет в этой среде")
    text = path.read_text(encoding="utf-8")
    assert reply_rules.ASSUMPTION_BOUNDARY_UK in text, (
        f"{slug}/playbook.md не несёт правила границы допущений")
    assert reply_rules.HISTORY_IS_NOT_A_SOURCE_UK in text, (
        f"{slug}/playbook.md не несёт правила про историю диалога")
    assert reply_rules.COMPLAINT_HANDOFF_UK in text, (
        f"{slug}/playbook.md не даёт РАЗРЕШЁННОЙ формулировки передачи по "
        f"претензии — остаётся один запрет, и автор придумает замену сам")


def test_a_generated_playbook_carries_the_rules_too():
    """Тот же вопрос к пайплайну онбординга: новый клиент рождается с правилом.

    Иначе правило есть у двоих сегодняшних и не будет у третьего, а разница
    вскроется на живом лиде.
    """
    from tests.test_onboard_render import SLUG, make_brief
    from chatter.onboard import render

    result = render.render_all(make_brief(), slug=SLUG)
    playbook = dict(result.files).get("playbook.md") or dict(result.files).get("playbook")
    assert playbook, "генератор не отдал playbook — сторож ослеп"
    assert reply_rules.ASSUMPTION_BOUNDARY_UK in playbook, (
        "сгенерированный playbook не несёт правила границы допущений")
    assert reply_rules.HISTORY_IS_NOT_A_SOURCE_UK in playbook, (
        "сгенерированный playbook не несёт правила про историю диалога")


def test_the_playbook_rule_and_the_prompt_rule_are_the_same_string():
    """Тот же S2 с другой стороны: в playbook едет ТА ЖЕ строка.

    Сравниваются не «похожие» тексты, а объекты одного источника: если
    завтра кто-то положит в генератор playbook «уточнённую» редакцию,
    сравнение перестанет быть тождеством.
    """
    assert reply_rules.playbook_rules_block_uk().count(
        reply_rules.ASSUMPTION_BOUNDARY_UK) == 1, (
        "блок правил для playbook не содержит ровно ту же строку правила")
    assert reply_rules.playbook_rules_block_uk().count(
        reply_rules.HISTORY_IS_NOT_A_SOURCE_UK) == 1, (
        "блок правил для playbook не содержит ровно ту же строку про историю")


# ═════════════════════════════════════════════════════════════════════════════
# S3, S4. Обороты претензии ловятся ДЕТЕРМИНИРОВАННО
#
# §2. Повод — живая реплика Ярины: «мушу підключити старшого майстра — він
# розбереться в деталях і вирішить питання компенсації». «Вирішить питання
# компенсації» читается как «компенсация уже согласована», то есть бот выдал
# решение, которого никто не принимал.
# ═════════════════════════════════════════════════════════════════════════════

KNOWLEDGE_NO_COMPENSATION = (
    "Детейлінг-мийка — 1200 грн.\n"
    "Працюємо з 9:00 до 20:00.\n"
)


def _det(reply: str, incoming: str = "хочу поскаржитись на якість"):
    return deterministic_escalation(
        incoming_text=incoming, reply=reply,
        knowledge=KNOWLEDGE_NO_COMPENSATION, keywords=["скарга", "поскаржитись"],
        owner_id="Старший майстер", owner_ref="старшим майстром")


@pytest.mark.parametrize("phrase", [
    "вирішить питання компенсації",
    "повернемо гроші",
    "зробимо безкоштовно",
])
def test_a_complaint_promise_in_the_reply_is_caught_and_the_reply_is_suppressed(phrase):
    """S4 спеки. Мутация: снять оборот из списка.

    Требуется НЕ просто карточка владельцу, а `suppress`: карточка уходит
    владельцу, а ответ — лиду, и лид прочтёт обещание компенсации раньше, чем
    владелец успеет открыть телефон.
    """
    det = _det(f"Прошу вибачення. Старший майстер {phrase} найближчим часом.")
    assert det is not None, (
        f"оборот претензии «{phrase}» прошёл мимо детерминированного слоя: "
        f"уговор в промпте — это не проверка, он держится добротой модели")
    assert det.suppress, (
        f"«{phrase}» эскалировано, но ответ НЕ подавлен ({det.tag}): лид "
        f"прочитает обещание компенсации, а владелец узнает об этом после")
    # 🔴 Тег обязателен, и вот почему. «Безкоштовн» УЖЕ есть в
    # `DEFAULT_PROMISE_TERMS`, то есть «зробимо безкоштовно» ловится старым
    # слоем необеспеченных обещаний и БЕЗ этой спеки. Сторож без тега зеленел
    # бы чужой заслугой, а мутация «снять оборот из списка» его не покраснила
    # бы — то самое слепое пятно, ради которого мутационный гейт и заведён.
    # Плюс тег — это строка «почему» в карточке владельцу: «обещание вне базы»
    # и «обещал компенсацию по претензии» требуют разных действий от человека.
    assert det.tag == reply_rules.COMPLAINT_TAG, (
        f"«{phrase}» поймано слоем {det.tag!r}, а не правилом претензии: "
        f"сторож проверяет чужую проверку и промолчит, когда правило снимут")


def test_the_complaint_dictionary_is_deterministic_not_a_prompt_plea():
    """S3 спеки. Мутация: оставить только уговор в промпте.

    Список обязан быть данными в коде, а не строкой инструкции: иначе его
    нельзя ни проверить, ни расширить без правки промпта.
    """
    assert reply_rules.COMPLAINT_FORBIDDEN_UK, "список оборотов претензии пуст"
    for phrase in reply_rules.COMPLAINT_FORBIDDEN_UK:
        assert phrase == phrase.casefold(), (
            f"«{phrase}» хранится не в нормализованном виде — сверка по "
            f"регистру пропустит «Повернемо Гроші»")


def test_a_capitalised_complaint_promise_is_caught_too():
    """Обратная сторона нормализации: клиентский текст пишется как угодно."""
    det = _det("ПОВЕРНЕМО ГРОШІ протягом трьох днів.")
    assert det is not None and det.suppress, (
        "оборот в верхнем регистре прошёл: сверка регистрозависима")


# ── S5: нейтральная передача обязана ПРОХОДИТЬ ───────────────────────────────

@pytest.mark.parametrize("reply", [
    "Передаю старшому майстру — він розгляне вимогу і звʼяжеться з вами.",
    "Зафіксувала ситуацію. Старший майстер звʼяжеться з вами щодо цієї ситуації.",
])
def test_a_neutral_handoff_is_not_suppressed(reply):
    """S5 спеки. Мутация: запретить всё подряд.

    Правило §2 требует ПЕРЕДАТЬ человеку нейтральной формулировкой. Если
    подавляется и она, у бота не остаётся ни одного законного ответа на
    претензию — он замолчит там, где обязан передать.
    """
    det = _det(reply)
    suppressed = det is not None and det.suppress
    assert not suppressed, (
        f"нейтральная передача подавлена ({det.tag if det else None}): "
        f"«{reply}» — это ровно то, что бот обязан говорить в претензии")


def test_the_allowed_handoff_wording_is_offered_not_only_forbidden():
    """Правило, которое только запрещает, оставляет автора без формулировки."""
    assert reply_rules.COMPLAINT_HANDOFF_UK.strip(), (
        "разрешённая формулировка передачи не названа — запрет без замены "
        "заставляет придумывать её заново каждому, кто правит playbook")
    assert not any(bad in reply_rules.COMPLAINT_HANDOFF_UK.casefold()
                   for bad in reply_rules.COMPLAINT_FORBIDDEN_UK), (
        "разрешённая формулировка сама содержит запрещённый оборот")


# ═════════════════════════════════════════════════════════════════════════════
# S7. История диалога — НЕ источник фактов
#
# §4. Бот выдумал число один раз; оно попало в историю; на следующем ходу оно
# уже «своё» и звучит увереннее. Так одна ошибка становится фоном всего
# диалога. Приоритет: knowledge > история > догадка (её нет).
# ═════════════════════════════════════════════════════════════════════════════

def _deps_with_history(tmp_path, history_text: str):
    cfg = load_config(CLIENTS, "demo")
    store = Store(":memory:")
    store.get_or_create_contact("lead-1")
    store.add_message("lead-1", "assistant", history_text, 900.0)
    deps = Deps(
        cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=["ок"]), cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None,
    )
    deps.notifier = None
    deps.classify = None
    deps.escalation_keywords = []
    return deps


def test_a_number_invented_in_a_previous_reply_does_not_become_backed(tmp_path, monkeypatch):
    """S7 спеки. Мутация: подмешать историю в базу обеспеченных чисел.

    Сторож стоит на ШВЕ: перехватывает вызов детерминированного слоя и
    смотрит, ЧТО ему отдали как базу фактов. Проверка «ответ эскалирован»
    здесь слабее — она зеленела бы и на подмешанной истории, если число
    случайно не совпало.
    """
    deps = _deps_with_history(tmp_path, "Знижка 700 грн діє до кінця тижня.")
    seen: dict[str, object] = {}
    real = escalation_module.deterministic_escalation

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr("chatter.run.deterministic_escalation", spy)
    _escalation_pass(deps, "lead-1", incoming_text="а знижка ще діє?",
                     reply="Так, знижка 700 грн ще діє.", now=1000.0)

    assert seen, "детерминированный слой вообще не вызвали — сторож ослеп"
    assert seen["knowledge"] == deps.cfg.knowledge, (
        "базой обеспеченных фактов отдали НЕ knowledge клиента: в неё "
        "подмешано что-то ещё, и число, выдуманное ботом в прошлой реплике, "
        "станет обеспеченным просто потому, что он его уже сказал")
    assert "700" not in seen["knowledge"], (
        "число из истории доехало в базу фактов — это и есть тот случай, "
        "ради которого правило 4 написано")
