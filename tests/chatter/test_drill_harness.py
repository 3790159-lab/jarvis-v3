# -*- coding: utf-8 -*-
"""Дрил-харнесс v1 (спека 2026-07-25): сценарий, словарь проверок, факты.

Мотив: Д-10 из 4 ходов занял вечер, проверки жили в голове, а сценарий каждый
раз формулировался заново — два прогона несравнимы. Здесь машинная часть:
сценарий читается как диалог, проверки записаны, факты берутся из БД и лога.

Всё в этом файле — ЧИСТОЕ: ни Telegram, ни сети. Живой шов (карточка-суфлёр,
ожидание сигнала) — в scripts/drill_runner.py, и он проверяется отдельно.
"""
from __future__ import annotations

import pytest

from chatter.core.drill import (
    EXPECT_KEYS, CheckResult, DrillScenarioError, Facts, StepOutcome, check_step,
    parse_scenario, plan_lines, run_verdict, vacuous_expectations,
)

SCENARIO = """
name: Д-10 слот обязательств
client: volska
contact: "237616472:volska"
steps:
  - say: "Ми вирішили - робимо новий логотип з нуля"
    expect:
      profile_contains: ["новий лого"]
      obligations: { brief: delivered }
      classifier_errors: 0
  - say: "А що саме входить у вартість?"
    expect:
      cache: hit
      obligations_unchanged: true
"""


def _facts(**kw) -> Facts:
    base = dict(cache="hit", obligations={"brief": "delivered"},
                obligations_before={"brief": "delivered"}, profile="Чайна Гора",
                classifier_errors=0, cards_delivered=0, replies=1, process_ends=1)
    base.update(kw)
    return Facts(**base)


# ── сценарий читается как диалог ─────────────────────────────────────────────


def test_scenario_parses_steps_in_order():
    sc = parse_scenario(SCENARIO)
    assert sc.name.startswith("Д-10")
    assert sc.contact == "237616472:volska"
    assert [s.say for s in sc.steps] == [
        "Ми вирішили - робимо новий логотип з нуля",
        "А що саме входить у вартість?"]


def test_step_without_say_is_a_loud_error():
    """Шаг без реплики — это не дрил, а тишина: молчаливый пропуск скрыл бы,
    что половина сценария не выполнялась."""
    with pytest.raises(DrillScenarioError, match="say"):
        parse_scenario("name: x\ncontact: c\nsteps:\n  - expect: {cache: hit}\n")


def test_unknown_expect_key_is_rejected():
    """Словарь ЗАКРЫТ намеренно: как только понадобится произвольная проверка,
    её место в тестах, а не в дриле."""
    with pytest.raises(DrillScenarioError, match="magic_check"):
        parse_scenario('name: x\ncontact: c\nsteps:\n  - say: "hi"\n'
                       '    expect: {magic_check: 1}\n')


def test_scenario_without_steps_is_rejected():
    with pytest.raises(DrillScenarioError, match="steps"):
        parse_scenario("name: x\ncontact: c\nsteps: []\n")


def test_broken_yaml_is_rejected_with_context():
    with pytest.raises(DrillScenarioError):
        parse_scenario("name: [unclosed\n")


def test_expect_vocabulary_is_the_documented_one():
    assert EXPECT_KEYS == {
        "cache", "obligations", "obligations_unchanged", "profile_contains",
        "classifier_errors", "card_delivered", "no_duplicate_reply"}


# ── проверки: зелёный и красный случай на каждую ─────────────────────────────


def _verdict(expect, facts):
    res = check_step(expect, facts)
    return all(c.ok for c in res), res


def test_cache_hit_expectation():
    ok, _ = _verdict({"cache": "hit"}, _facts(cache="hit"))
    assert ok
    ok, res = _verdict({"cache": "hit"}, _facts(cache="miss"))
    assert not ok and "miss" in res[0].detail


def test_cache_miss_expectation_is_legitimate_too():
    """Первый ход после смены префикса ОБЯЗАН быть промахом — это ожидание,
    а не провал."""
    ok, _ = _verdict({"cache": "miss"}, _facts(cache="miss"))
    assert ok


def test_obligations_expectation_checks_named_keys_only():
    facts = _facts(obligations={"brief": "delivered", "owner_write": "delivered"})
    ok, _ = _verdict({"obligations": {"brief": "delivered"}}, facts)
    assert ok, "лишние обязательства не должны валить проверку названного"
    ok, res = _verdict({"obligations": {"brief": "open"}}, facts)
    assert not ok and "delivered" in res[0].detail


def test_missing_obligation_is_reported_not_crashed():
    ok, res = _verdict({"obligations": {"examples": "delivered"}},
                       _facts(obligations={"brief": "delivered"}))
    assert not ok and "нет" in res[0].detail


def test_obligations_unchanged():
    same = {"brief": "delivered"}
    ok, _ = _verdict({"obligations_unchanged": True},
                     _facts(obligations=same, obligations_before=dict(same)))
    assert ok
    ok, res = _verdict({"obligations_unchanged": True},
                       _facts(obligations={"brief": "open"},
                              obligations_before={"brief": "delivered"}))
    assert not ok and "изменил" in res[0].detail.lower()


def test_profile_contains_all_substrings():
    ok, _ = _verdict({"profile_contains": ["Чайна", "Гора"]},
                     _facts(profile="Чайна Гора, чай"))
    assert ok
    ok, res = _verdict({"profile_contains": ["референси"]},
                       _facts(profile="Чайна Гора"))
    assert not ok and "референси" in res[0].detail


def test_classifier_errors_budget():
    ok, _ = _verdict({"classifier_errors": 0}, _facts(classifier_errors=0))
    assert ok
    ok, res = _verdict({"classifier_errors": 0}, _facts(classifier_errors=1))
    assert not ok and "1" in res[0].detail


def test_card_delivered():
    ok, _ = _verdict({"card_delivered": True}, _facts(cards_delivered=1))
    assert ok
    ok, _ = _verdict({"card_delivered": False}, _facts(cards_delivered=0))
    assert ok
    ok, res = _verdict({"card_delivered": True}, _facts(cards_delivered=0))
    assert not ok


def test_no_duplicate_reply():
    ok, _ = _verdict({"no_duplicate_reply": True}, _facts(replies=3, process_ends=1))
    assert ok, "три баббла одного ответа — это НЕ дубль"
    ok, res = _verdict({"no_duplicate_reply": True},
                       _facts(replies=2, process_ends=2))
    assert not ok and "process END" in res[0].detail


def test_silent_turn_is_a_failure_for_no_duplicate_reply():
    """Ход обработан, но лид не получил НИЧЕГО — худший вид «зелёного»."""
    ok, res = _verdict({"no_duplicate_reply": True},
                       _facts(replies=0, process_ends=1))
    assert not ok and "0" in res[0].detail


def test_all_checks_run_even_after_the_first_failure():
    """Отчёт должен показывать ВСЮ картину хода, а не первую ошибку."""
    res = check_step({"cache": "hit", "classifier_errors": 0,
                      "profile_contains": ["нет-такого"]},
                     _facts(cache="miss", classifier_errors=2, profile="x"))
    assert len(res) == 3 and not any(c.ok for c in res)


def test_empty_expect_is_a_neutral_step():
    """Шаг «просто отправь и посмотри» — законный: не всякий ход проверяем."""
    assert check_step({}, _facts()) == []


# ── вердикт прогона: пропуск шага = прогон НЕ состоялся ──────────────────────
# Первый прогон 2026-07-25 вышел с кодом 0, хотя 3 шага из 4 были пропущены по
# таймауту: гейт считал только красные проверки, а «шага не было» проверкой не
# считалось. Отчёт с одним зелёным шагом из четырёх нельзя предъявлять на
# приёмке, и автоматика не должна принимать его за успех.


def test_skipped_step_means_the_run_did_not_happen():
    v = run_verdict([StepOutcome(say="1", checks=(CheckResult("cache", True, ""),)),
                     StepOutcome(say="2", skipped=True),
                     StepOutcome(say="3", skipped=True)])
    assert v.code == 2, "пропуск шага обязан давать НЕНУЛЕВОЙ код выхода"
    assert "НЕ СОСТОЯЛСЯ" in v.headline
    assert "2 из 3" in v.detail


def test_skipped_step_outranks_green_checks():
    """Все выполненные проверки зелёные — но прогон всё равно не состоялся."""
    v = run_verdict([StepOutcome(say="1", checks=(CheckResult("cache", True, "ok"),)),
                     StepOutcome(say="2", skipped=True)])
    assert v.code == 2


def test_failed_check_on_a_full_run_is_code_one():
    v = run_verdict([StepOutcome(say="1", checks=(CheckResult("cache", False, "ждали hit"),))])
    assert v.code == 1
    assert "КРАСН" in v.headline.upper()


def test_full_green_run_is_code_zero():
    v = run_verdict([StepOutcome(say="1", checks=(CheckResult("cache", True, "ok"),)),
                     StepOutcome(say="2", checks=())])
    assert v.code == 0
    assert "ЗЕЛЁНЫЙ" in v.headline


def test_step_without_checks_is_not_a_skip():
    """«Отправь и посмотри» — законный шаг: он СОСТОЯЛСЯ, просто нечего сверять.
    Путать его с пропуском значит красить молчание в зелёный."""
    v = run_verdict([StepOutcome(say="1", checks=())])
    assert v.code == 0


# ── суфлёр: план целиком до старта ───────────────────────────────────────────


def test_plan_lists_every_replica_with_its_number():
    sc = parse_scenario(SCENARIO)
    plan = plan_lines(sc)
    assert len(plan) == 2
    assert plan[0].startswith("1.") and "Ми вирішили" in plan[0]
    assert plan[1].startswith("2.") and "вартість" in plan[1]


def test_plan_marks_steps_that_need_more_than_a_replica():
    """На шаге с карточкой от владельца ждут тап — он должен знать это ДО
    старта, а не в момент, когда прогон уже висит на таймауте."""
    sc = parse_scenario("name: x\ncontact: c\nsteps:\n"
                        "  - say: \"оплата\"\n    expect: {card_delivered: true}\n")
    assert "карточк" in plan_lines(sc)[0].lower()


# ── пустые проверки видны ДО старта ──────────────────────────────────────────
# Слот обязательств у живого контакта уже мог быть закрыт прошлым прогоном.
# Тогда `obligations: {owner_write: delivered}` пройдёт, ничего не доказав, —
# и об этом надо знать до того, как прогон спишет деньги, а не после.


def test_expectation_already_satisfied_before_the_run_is_flagged():
    sc = parse_scenario("name: x\ncontact: c\nsteps:\n"
                        "  - say: \"оплата\"\n"
                        "    expect: {obligations: {owner_write: delivered}}\n")
    warns = vacuous_expectations(sc, {"owner_write": "delivered"})
    assert len(warns) == 1
    assert "owner_write" in warns[0] and "1" in warns[0]


def test_expectation_that_still_has_to_happen_is_not_flagged():
    sc = parse_scenario("name: x\ncontact: c\nsteps:\n"
                        "  - say: \"оплата\"\n"
                        "    expect: {obligations: {owner_write: delivered}}\n")
    assert vacuous_expectations(sc, {"owner_write": "open"}) == []
    assert vacuous_expectations(sc, {}) == []


# ── «создан на этом ходе» vs «закрыт к этому ходу» — разные проверки ─────────
# Обязательство может родиться и закрыться ОДНИМ ходом (лид просит пересчёт,
# бот тут же называет сумму = recalc сразу delivered), а может провисеть до
# следующего. Требовать ровно один статус — значит красить нормальное
# поведение в красное; поэтому статус умеет альтернативу «open|delivered».


def test_obligation_status_accepts_an_alternative():
    for actual in ("open", "delivered"):
        ok, _ = _verdict({"obligations": {"recalc": "open|delivered"}},
                         _facts(obligations={"recalc": actual}))
        assert ok, f"{actual} входит в альтернативу и обязан проходить"


def test_alternative_does_not_swallow_a_wrong_status():
    ok, res = _verdict({"obligations": {"recalc": "open|delivered"}},
                       _facts(obligations={"recalc": "cancelled"}))
    assert not ok
    assert "cancelled" in res[0].detail and "open|delivered" in res[0].detail


def test_alternative_still_requires_the_obligation_to_exist():
    """Ход, который ОБЯЗАН был завести долг, но не завёл, — это провал, а не
    «ну, статуса нет — значит любой подойдёт»."""
    ok, res = _verdict({"obligations": {"recalc": "open|delivered"}},
                       _facts(obligations={}))
    assert not ok and "нет в слоте" in res[0].detail


def test_alternative_is_flagged_vacuous_only_when_already_matching():
    sc = parse_scenario("name: x\ncontact: c\nsteps:\n  - say: \"порахуйте\"\n"
                        "    expect: {obligations: {recalc: open|delivered}}\n")
    assert vacuous_expectations(sc, {"recalc": "delivered"})
    assert vacuous_expectations(sc, {"recalc": "cancelled"}) == []
    assert vacuous_expectations(sc, {}) == []
