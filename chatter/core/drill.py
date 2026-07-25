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
    obligations: dict               # {okey: status} после хода — ВСЕ строки
    obligations_before: dict        # до хода
    profile: str
    classifier_errors: int
    cards_delivered: int
    replies: int                    # сколько OUT ушло лиду
    process_ends: int               # сколько раз ход завершился
    # Только долги БОТА. После фикса P17 клиентская заметка («клієнт ще не
    # обрав спосіб оплати») — законная запись с owed_by=client; она не долг
    # бота, и «слот не шевельнулся» смотрит именно сюда. Дефолт пустой —
    # старые фикстуры продолжают собираться.
    obligations_bot: dict = field(default_factory=dict)
    obligations_bot_before: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    key: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class StepOutcome:
    """Итог одного шага. `skipped` отделён от «нет проверок» намеренно: шаг
    «отправь и посмотри» СОСТОЯЛСЯ, а пропущенный по таймауту — нет, и
    складывать их в одну корзину значит красить молчание в зелёный."""
    say: str
    checks: tuple[CheckResult, ...] = ()
    skipped: bool = False
    note: str = ""

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.ok)


@dataclass(frozen=True)
class Verdict:
    code: int          # 0 зелёный · 1 есть красное · 2 прогон НЕ состоялся
    headline: str
    detail: str


def run_verdict(outcomes) -> Verdict:
    """Гейт прогона. Пропуск шага ПЕРЕВЕШИВАЕТ зелёные проверки: первый прогон
    харнесса (2026-07-25) вышел с кодом 0, имея 1 выполненный шаг из 4, потому
    что «шага не было» проверкой не считалось. Отчёт по четверти сценария нельзя
    предъявлять на приёмке, и автоматика не должна принимать его за успех."""
    total = len(outcomes)
    skipped = sum(1 for o in outcomes if o.skipped)
    failed = sum(o.failed for o in outcomes if not o.skipped)
    if skipped:
        return Verdict(
            2, "🔴 ВЕРДИКТ: ПРОГОН НЕ СОСТОЯЛСЯ",
            f"пропущено {skipped} из {total} шаг(ов) — сценарий не пройден "
            f"целиком, результат не годится для приёмки")
    if failed:
        return Verdict(
            1, "🔴 ВЕРДИКТ: ЕСТЬ КРАСНОЕ",
            f"{total} шаг(ов) выполнено, провалено проверок: {failed}")
    return Verdict(0, "✅ ВЕРДИКТ: ПРОГОН ЗЕЛЁНЫЙ",
                   f"{total} из {total} шаг(ов), все проверки зелёные")


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


# --- суфлёр -----------------------------------------------------------------
# Весь сценарий печатается ДО старта. Прогон 2026-07-25 висел в фоновой команде,
# и реплики пришлось диктовать по одной вручную: план, известный только скрипту,
# бесполезен человеку у телефона.

_OWNER_ACTIONS = {
    "card_delivered": "придёт карточка владельцу — тапни решение",
    "obligations": "ждём изменения слота обязательств",
    "cache": None,  # заполняется отдельно: холодный ход стоит дороже
}


def owner_action(step: "Step") -> str:
    """Что ещё, кроме отправки реплики, ждут от владельца на этом шаге."""
    hints = []
    if step.expect.get("card_delivered"):
        hints.append(_OWNER_ACTIONS["card_delivered"])
    if "owner_write" in (step.expect.get("obligations") or {}):
        hints.append("ход требует ответа владельца (owner_write)")
    if step.expect.get("cache") == "miss":
        hints.append("холодный ход — платим за новый кэш")
    return "; ".join(hints)


def vacuous_expectations(scenario: "Scenario", before: dict) -> list[str]:
    """Проверки, которые пройдут ещё до того, как дрил что-то сделает.

    Дрил идёт по ЖИВОМУ контакту: слот обязательств мог быть закрыт прошлым
    прогоном или обычным разговором. Тогда `obligations: {owner_write:
    delivered}` зеленеет, ничего не доказав. Владелец должен знать это ДО
    старта — иначе прогон за $0.22 подтвердит сам себя."""
    out = []
    for i, s in enumerate(scenario.steps, 1):
        for okey, status in (s.expect.get("obligations") or {}).items():
            if before.get(okey) in _statuses(status):
                out.append(f"шаг {i}: obligations {okey}={status} — уже так ДО прогона, "
                           f"проверка ничего не докажет")
    return out


def plan_lines(scenario: "Scenario") -> list[str]:
    out = []
    for i, s in enumerate(scenario.steps, 1):
        action = owner_action(s)
        checks = ", ".join(sorted(s.expect)) or "без проверок"
        out.append(f"{i}. «{s.say}»\n     проверки: {checks}"
                   + (f"\n     ⚠️ {action}" if action else ""))
    return out


# --- проверки ---------------------------------------------------------------
# Все они выполняются ЦЕЛИКОМ, даже если первая же провалилась: отчёт должен
# показывать всю картину хода, иначе на второй прогон уходит ещё один вечер.


def _check_cache(want: str, f: Facts) -> CheckResult:
    return CheckResult("cache", f.cache == want,
                       f"ожидали {want}, факт {f.cache}")


def _statuses(want: str) -> set[str]:
    """`open|delivered` — законное ожидание, а не размытость: обязательство может
    родиться и закрыться ОДНИМ ходом (лид просит пересчёт, бот тут же называет
    сумму), а может провисеть до следующего. Оба исхода — норма."""
    return {s.strip() for s in str(want).split("|") if s.strip()}


def _check_obligations(want: dict, f: Facts) -> CheckResult:
    bad = []
    for okey, status in (want or {}).items():
        have = f.obligations.get(okey)
        if have is None:
            bad.append(f"{okey}: нет в слоте (ждали {status})")
        elif have not in _statuses(status):
            bad.append(f"{okey}: {have}, ждали {status}")
    return CheckResult("obligations", not bad,
                       "; ".join(bad) or "совпало")


def _check_unchanged(want: bool, f: Facts) -> CheckResult:
    # Сравниваем ДОЛГИ БОТА, а не весь слот: клиентская заметка (owed_by=client)
    # — законная запись, которая ботом не отрабатывается и «шевелением слота»
    # не является. До фикса P17 таких записей просто не было — все other молча
    # становились долгом бота, и разницы между «весь слот» и «долги бота» тоже.
    before, after = f.obligations_bot_before, f.obligations_bot
    changed = after != before
    ok = (not changed) if want else changed
    return CheckResult("obligations_unchanged", ok,
                       f"долги бота изменились: {before} → {after}"
                       if changed else "долги бота не тронуты")


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
