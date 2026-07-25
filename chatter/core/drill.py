"""Дрил-харнесс v1: сценарий и проверки (спека 2026-07-25).

Чистый слой: ни Telegram, ни БД, ни файлов. Здесь живут ровно две вещи —
разбор сценария и вычисление вердикта хода по СНЯТЫМ фактам. Сбор фактов и
живой шов (карточка-суфлёр, ожидание сигнала) — в `scripts/drill_runner.py`.

Зачем вообще: Д-10 из четырёх ходов занял вечер, проверки жили в голове
разработчика, а реплики каждый раз формулировались заново — два прогона
несравнимы, регресс-прогон невозможен. Сценарий-файл делает дрил
воспроизводимым, а записанные проверки — предъявляемыми на приёмке.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

# Словарь проверок ЗАКРЫТ намеренно. Как только понадобится произвольная
# проверка, это сигнал, что она должна жить в тестах, а не в дриле: дрил
# отвечает на вопрос «ведёт ли себя живая модель как договорились», а не
# «работает ли код».
EXPECT_KEYS = {
    "cache",                  # hit | miss — читался ли кэш классификатора
    "obligations",            # {okey: status} — состояние слота ПОСЛЕ хода
    "obligations_unchanged",  # слот не шевельнулся
    "profile_contains",       # подстроки в свежем профиле
    "classifier_errors",      # допустимое число сбоев классификатора за ход
    "card_delivered",         # появилась ли карточка владельцу
    "no_duplicate_reply",     # ровно один обработанный ход и лид не остался без ответа
}


class DrillScenarioError(Exception):
    """Битый сценарий = громкая ошибка. Молчаливый пропуск шага скрыл бы, что
    половина дрила не выполнялась, а отчёт всё равно вышел бы зелёным."""


@dataclass(frozen=True)
class Step:
    say: str
    expect: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    name: str
    contact: str
    steps: tuple[Step, ...]
    client: str = ""


@dataclass(frozen=True)
class Facts:
    """Снимок хода. Собирается read-only из БД и лога раннера."""
    cache: str                      # hit | miss | n/a
    obligations: dict               # {okey: status} после хода
    obligations_before: dict        # до хода
    profile: str
    classifier_errors: int
    cards_delivered: int
    replies: int                    # сколько OUT ушло лиду
    process_ends: int               # сколько раз ход завершился


@dataclass(frozen=True)
class CheckResult:
    key: str
    ok: bool
    detail: str


def parse_scenario(text: str) -> Scenario:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DrillScenarioError(f"некорректный YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise DrillScenarioError("сценарий должен быть объектом с ключами name/contact/steps")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise DrillScenarioError("нужен непустой список steps")

    steps = []
    for i, s in enumerate(steps_raw, 1):
        if not isinstance(s, dict) or not str(s.get("say", "")).strip():
            raise DrillScenarioError(f"шаг {i}: нужен непустой 'say' (реплика лида)")
        expect = s.get("expect") or {}
        if not isinstance(expect, dict):
            raise DrillScenarioError(f"шаг {i}: 'expect' должен быть объектом")
        unknown = sorted(set(expect) - EXPECT_KEYS)
        if unknown:
            raise DrillScenarioError(
                f"шаг {i}: неизвестные проверки {', '.join(unknown)}; "
                f"допустимы: {', '.join(sorted(EXPECT_KEYS))}")
        steps.append(Step(say=str(s["say"]).strip(), expect=dict(expect)))

    return Scenario(name=str(raw.get("name", "дрил")),
                    contact=str(raw.get("contact", "")),
                    client=str(raw.get("client", "")),
                    steps=tuple(steps))


# --- проверки ---------------------------------------------------------------
# Все они выполняются ЦЕЛИКОМ, даже если первая же провалилась: отчёт должен
# показывать всю картину хода, иначе на второй прогон уходит ещё один вечер.


def _check_cache(want: str, f: Facts) -> CheckResult:
    return CheckResult("cache", f.cache == want,
                       f"ожидали {want}, факт {f.cache}")


def _check_obligations(want: dict, f: Facts) -> CheckResult:
    bad = []
    for okey, status in (want or {}).items():
        have = f.obligations.get(okey)
        if have is None:
            bad.append(f"{okey}: нет в слоте (ждали {status})")
        elif have != status:
            bad.append(f"{okey}: {have}, ждали {status}")
    return CheckResult("obligations", not bad,
                       "; ".join(bad) or "совпало")


def _check_unchanged(want: bool, f: Facts) -> CheckResult:
    changed = f.obligations != f.obligations_before
    ok = (not changed) if want else changed
    return CheckResult("obligations_unchanged", ok,
                       f"слот изменился: {f.obligations_before} → {f.obligations}"
                       if changed else "слот не тронут")


def _check_profile(want, f: Facts) -> CheckResult:
    missing = [s for s in (want or []) if s not in (f.profile or "")]
    return CheckResult("profile_contains", not missing,
                       f"в профиле нет: {', '.join(missing)}" if missing else "всё на месте")


def _check_classifier_errors(want: int, f: Facts) -> CheckResult:
    return CheckResult("classifier_errors", f.classifier_errors <= int(want),
                       f"сбоев {f.classifier_errors}, допускали {want}")


def _check_card(want: bool, f: Facts) -> CheckResult:
    got = f.cards_delivered > 0
    return CheckResult("card_delivered", got is bool(want),
                       f"карточек {f.cards_delivered}, ждали "
                       f"{'хотя бы одну' if want else 'ни одной'}")


def _check_no_duplicate(want: bool, f: Facts) -> CheckResult:
    # Три баббла одного ответа — НЕ дубль (это хуманайзер). Дубль — это два
    # завершённых хода на одну реплику. Ноль ответов при обработанном ходе —
    # худший вид «зелёного»: лид не получил ничего.
    if f.process_ends != 1:
        return CheckResult("no_duplicate_reply", not want,
                           f"process END: {f.process_ends} (ждали 1)")
    if f.replies < 1:
        return CheckResult("no_duplicate_reply", not want,
                           f"ход обработан, но лиду ушло 0 сообщений")
    return CheckResult("no_duplicate_reply", bool(want),
                       f"1 ход, {f.replies} сообщени(й) лиду")


_CHECKS = {
    "cache": _check_cache,
    "obligations": _check_obligations,
    "obligations_unchanged": _check_unchanged,
    "profile_contains": _check_profile,
    "classifier_errors": _check_classifier_errors,
    "card_delivered": _check_card,
    "no_duplicate_reply": _check_no_duplicate,
}


def check_step(expect: dict, facts: Facts) -> list[CheckResult]:
    """Вердикт по одному ходу. Порядок — как в EXPECT_KEYS, чтобы отчёты двух
    прогонов сравнивались построчно."""
    return [_CHECKS[k](expect[k], facts)
            for k in sorted(expect, key=lambda x: sorted(EXPECT_KEYS).index(x))]
