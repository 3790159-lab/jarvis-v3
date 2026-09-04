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

from chatter.core import sales_checks
from chatter.core.guardrails import _REDACTION_DEADLINE, _REDACTION_PRICE
from chatter.core.langdetect import detect_language

# Константы редакции — тот самый набор, который отчёт увидел трижды в
# одной реплике. Берём ИЗ КОДА, а не переписываем: разойдутся — проверка
# станет проверять собственную копию.
_FORMULAS = tuple(_REDACTION_PRICE.values()) + tuple(_REDACTION_DEADLINE.values())

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
    # --- «продажность» (спека sales-competence §6.1). Открыт ровно на неё:
    # все девять — про поведение ЖИВОЙ модели, то есть по назначению дрила.
    "answers_before_escalating",  # есть содержание ДО упоминания владельца
    "uses_lead_numbers",          # перечисленные числа лида прозвучали
    "offer_range",                # назван диапазон + валюта
    "questions_count",            # "2..4" — не допрос и не пустой ход
    "next_step_with_sla",         # следующий шаг со сроком в ПОСЛЕДНЕЙ фразе
    "no_escalation",              # карточки нет И владелец не помянут
    "reply_language",             # uk | ru — ловит смешение языков
    "empathy_max",                # счётчик эмпатии ЗА ДИАЛОГ
    "no_repeated_formula",        # ни одна константа не вставлена дважды
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
    # --- факты «продажности». Умолчания пустые: старые фикстуры и старые
    # сценарии обязаны собираться и считаться ровно как раньше.
    reply_text: str = ""            # что РЕАЛЬНО ушло лиду за ход
    lead_numbers: tuple = ()        # числа, произнесённые лидом в окне
    knowledge_numbers: frozenset = frozenset()
    owner_marks: tuple = ()         # как в этом клиенте зовут владельца
    empathy_so_far: int = 0         # эмпатические зачины ЗА ДИАЛОГ


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

    # DEV-32, вариант 3: сценарий, шаг которого не опознаёт САМ СЕБЯ, обречён
    # покраснеть или разъехаться на шаг — и узнают об этом уже потратив деньги
    # и время человека. Отказ обязан быть ДО старта и громким; иначе судья
    # припишет проверки чужому ходу молча (прогон №5, 26.07).
    ordered = tuple(steps)
    for i, step in enumerate(ordered):
        got = match_step(step.say, ordered)
        if got == i:
            continue
        where = (f"шаг {got + 1} «{ordered[got].say}»" if got is not None
                 else "НИ ОДИН шаг")
        raise DrillScenarioError(
            f"шаг {i + 1} «{step.say}» опознаётся как {where} — судья припишет "
            f"проверки чужому ходу. Сделай реплику отличимой от соседних")

    return Scenario(name=str(raw.get("name", "дрил")),
                    contact=str(raw.get("contact", "")),
                    client=str(raw.get("client", "")),
                    steps=ordered)


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


def _normalize_say(text: str) -> str:
    """Реплика без того, что не меняет смысла: регистр, пунктуация, тире,
    эмодзи, лишние пробелы. Владелец печатает с телефона, и «Дякую, чекаю!!!»
    — та же реплика, что «Дякую, чекаю»."""
    keep = [ch.lower() if (ch.isalnum() or ch.isspace()) else " "
            for ch in (text or "")]
    return " ".join("".join(keep).split())


def match_step(text: str, steps, *, threshold: float = 0.72) -> int | None:
    """Какому шагу сценария соответствует РЕАЛЬНО отправленная реплика.

    Прогон №5 (2026-07-26) разъехался на шаг: харнесс ждал «любое новое
    сообщение лида», владелец опоздал на первый шаг — и дальше проверки шага N
    применялись к ходу шага N−1. Зелёное и красное в отчёте перестали
    относиться к тому, что в нём написано. Поэтому шаг опознаётся ПО ТЕКСТУ,
    а курсор переставляется на реально отправленную реплику.

    Сравнение нестрогое: одна опечатка не повод рвать прогон. Возвращает индекс
    шага либо None, если реплика не похожа ни на один (посторонняя фраза не
    должна молча притвориться шагом сценария)."""
    from difflib import SequenceMatcher

    want = _normalize_say(text)
    if want:
        scores = [SequenceMatcher(None, want, _normalize_say(s.say)).ratio()
                  for s in steps]
    else:
        # DEV-32. Нормализация выбрасывает всё, кроме букв и цифр, поэтому у
        # «🔥👍», «...», «!!!» она пуста — и прежний `return None` объявлял
        # ЛЮБУЮ бесбуквенную реплику непознаваемой. Живьём 17.08 это дало
        # красное на шаге, который бот отработал безупречно: красное
        # относилось к судье, а не к боту.
        #
        # Запасной путь сравнивает СЫРЫЕ строки — тем же `SequenceMatcher` и
        # тем же порогом, поэтому «👍» и «...» остаются РАЗНЫМИ репликами, а
        # посторонняя фраза по-прежнему не притворяется шагом. Ослабления
        # инварианта здесь нет: изменился текст сравнения, не строгость.
        raw = " ".join((text or "").split())
        if not raw:
            return None
        scores = [SequenceMatcher(None, raw, " ".join((s.say or "").split())).ratio()
                  for s in steps]
    if not scores:
        return None
    best = max(range(len(scores)), key=lambda i: scores[i])
    return best if scores[best] >= threshold else None


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


def _fmt(ok: bool, key: str, detail: str) -> CheckResult:
    return CheckResult(key, ok, detail)


def _check_answers_before(want: bool, f: Facts) -> CheckResult:
    got = sales_checks.answers_before_escalating(
        f.reply_text, knowledge_numbers=f.knowledge_numbers,
        lead_nums=f.lead_numbers, owner_marks=f.owner_marks)
    return _fmt(got == bool(want), "answers_before_escalating",
                "содержание до упоминания владельца: %s (ждали %s)" % (got, want))


def _check_uses_lead_numbers(want, f: Facts) -> CheckResult:
    want = [str(x) for x in (want or [])]
    got = sales_checks.uses_lead_numbers(f.reply_text, want)
    missing = [n for n in want if n not in got]
    return _fmt(not missing, "uses_lead_numbers",
                "не прозвучали: %s" % (", ".join(missing) or "—"))


def _check_offer_range(want: bool, f: Facts) -> CheckResult:
    got = sales_checks.offer_range(f.reply_text)
    return _fmt(got == bool(want), "offer_range",
                "диапазон в ответе: %s (ждали %s)" % (got, want))


def _check_questions_count(want, f: Facts) -> CheckResult:
    got = sales_checks.questions_count(f.reply_text)
    text = str(want)
    if ".." in text:
        lo, hi = (int(x) for x in text.split(".."))
    else:
        lo = hi = int(text)
    return _fmt(lo <= got <= hi, "questions_count",
                "вопросов %d, ждали %s" % (got, text))


def _check_next_step_sla(want: bool, f: Facts) -> CheckResult:
    got = sales_checks.next_step_with_sla(f.reply_text)
    return _fmt(got == bool(want), "next_step_with_sla",
                "срок в последней фразе: %s (ждали %s)" % (got, want))


def _check_no_escalation(want: bool, f: Facts) -> CheckResult:
    low = (f.reply_text or "").casefold()
    named = any(m and m.casefold() in low for m in f.owner_marks)
    got = (f.cards_delivered == 0) and not named
    return _fmt(got == bool(want), "no_escalation",
                "карточек %d, владелец помянут: %s" % (f.cards_delivered, named))


def _check_reply_language(want: str, f: Facts) -> CheckResult:
    got = detect_language(f.reply_text or "", default="")
    return _fmt(got == str(want), "reply_language",
                "язык ответа %r, ждали %r" % (got, want))


def _check_empathy_max(want: int, f: Facts) -> CheckResult:
    return _fmt(f.empathy_so_far <= int(want), "empathy_max",
                "эмпатии за диалог %d, потолок %s" % (f.empathy_so_far, want))


def _check_no_repeated_formula(want: bool, f: Facts) -> CheckResult:
    dup = sales_checks.repeated_formula(f.reply_text, _FORMULAS)
    got = dup is None
    return _fmt(got == bool(want), "no_repeated_formula",
                "повторена формула: %r" % (dup,) if dup else "повторов нет")


# Регистрируем ПОСЛЕ определений: таблица объявлена выше, и дописывать её
# там значило бы ссылаться на ещё не существующие функции.
_CHECKS.update({
    "answers_before_escalating": _check_answers_before,
    "uses_lead_numbers": _check_uses_lead_numbers,
    "offer_range": _check_offer_range,
    "questions_count": _check_questions_count,
    "next_step_with_sla": _check_next_step_sla,
    "no_escalation": _check_no_escalation,
    "reply_language": _check_reply_language,
    "empathy_max": _check_empathy_max,
    "no_repeated_formula": _check_no_repeated_formula,
})

# Ни один ключ словаря не имеет права остаться без реализации: сценарий с
# необслуженным ключом молча не проверял бы ничего.
assert set(_CHECKS) == EXPECT_KEYS, sorted(EXPECT_KEYS - set(_CHECKS))


def check_step(expect: dict, facts: Facts) -> list[CheckResult]:
    """Вердикт по одному ходу. Порядок — как в EXPECT_KEYS, чтобы отчёты двух
    прогонов сравнивались построчно."""
    return [_CHECKS[k](expect[k], facts)
            for k in sorted(expect, key=lambda x: sorted(EXPECT_KEYS).index(x))]
