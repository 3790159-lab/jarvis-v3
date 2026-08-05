# -*- coding: utf-8 -*-
"""Ш1 яруса 2: детекторы как ЧИСТЫЕ функции над снимком.

Спека: `docs/superpowers/specs/2026-08-05-autonomy-tier2-proposal-cards.md`.

Правила модуля, которые держатся тестами:
  * ни БД, ни сети, ни файловой системы, ни подпроцессов — снимок приходит
    аргументом, собирает его отдельный (грязный) сборщик;
  * в `evidence` попадают только СТАБИЛЬНЫЕ факты: изменчивое поле меняет
    хеш и на каждом прогоне рождает «новое наблюдение» (шум растёт линейно);
  * уровень действия резолвится из политики с fail-closed дефолтом 4.

Различение «дыра» и «просто нет таска» — главная работа первого детектора.
Разведка 06.08 показала, что из пяти кандидатов два ложные, и без фильтра
теневая неделя стартовала бы с 40% шума при гейте 20%.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.services.autonomy_proposals import Proposal

#: Fail-closed: неизвестное действие считается запрещённым (уровень 4), а не
#: «наверное можно» — тот же контракт, что у машиночитаемой политики.
FAIL_CLOSED_LEVEL = 4

#: Наше имя действия -> имя в политике безопасности.
#: `register_missing_task` — узкое действие из §8 спеки (восстановить триггер
#: у известного таска фермы). В действующей политике его ЕЩЁ НЕТ, поэтому
#: резолвится в fail-closed 4 — так и должно быть до решения владельца.
ACTION_TO_POLICY = {
    "register_scheduled_task": "scheduled_task_mutation",
    "register_missing_task": "register_missing_task",
}

#: Коды `LastTaskResult`, которые НЕ являются падением. Сняты с живой фермы
#: 06.08, иначе теневой набор зашумел бы на здоровой системе:
#:   0          — успех;
#:   267009     — задача выполняется прямо сейчас (0x41301);
#:   267011     — задача ни разу не запускалась (0x41303), свежий таск;
#:   2147946720 — 0x800710E0, отдаётся у трёх РАБОТАЮЩИХ гардианов фермы.
NON_FAILURE_RESULTS = (0, 267009, 267011, 2147946720)


@dataclass(frozen=True)
class RegisterScript:
    """Факты об одном register-скрипте на момент снимка.

    `task_name=None` означает «скрипт не регистрирует Scheduled Task вовсе»
    (так устроен `register_telegram_webhook.ps1` — он переключает бота на
    webhook через Bot API). `feature_active=False` означает «фича спит»:
    у `ig_schedule_publisher` не существует очереди, публиковать нечего.
    """

    script: str
    task_name: str | None
    task_present: bool
    feature_active: bool
    inactive_reason: str = ""


@dataclass(frozen=True)
class TaskSnapshot:
    """Факты об одном Scheduled Task на момент снимка.

    `expected_repetition` приходит из белого списка фермы, а не выводится из
    самого таска: «триггера нет» — дыра только там, где триггер ждали.
    `JarvisSniperDetached` отставлен намеренно, `JarvisInfraRestartCloudflared`
    on-demand по спеке — у обоих ожидание False.

    `refusal_codes` — коды, которыми таск сообщает ЧЕСТНЫЙ ОТКАЗ, а не крах
    (у дрил-обёртки это 2: «прогон не состоялся»). Показать стоит, назвать
    крахом — нельзя.
    """

    name: str
    state: str
    triggers: tuple[str, ...]
    repetition_interval: str | None
    expected_repetition: bool
    last_results: tuple[int, ...] = ()
    refusal_codes: tuple[int, ...] = ()
    note: str = ""


def resolve_action_level(action: str, policy_levels: Mapping[str, int]) -> int:
    """Уровень действия по политике. Неизвестное действие -> 4."""
    level = policy_levels.get(action)
    if isinstance(level, bool) or not isinstance(level, int) or level not in (0, 1, 2, 3, 4):
        return FAIL_CLOSED_LEVEL
    return level


def detect_register_scripts_without_task(
        snapshot: Mapping[str, Any],
        policy_levels: Mapping[str, int] | None = None) -> list[Proposal]:
    """Register-скрипт есть, живого таска нет — и это действительно дыра.

    Пропускаем: скрипты, которые таск не создают; уже живые таски; спящие
    фичи. Каждое исключение — не вкусовщина, а факт из снимка.
    """
    policy = policy_levels if policy_levels is not None else {}
    level = resolve_action_level(
        ACTION_TO_POLICY["register_scheduled_task"], policy)
    out: list[Proposal] = []
    for item in snapshot.get("register_scripts", ()):
        if item.task_name is None:
            continue
        if item.task_present:
            continue
        if not item.feature_active:
            continue
        out.append(Proposal(
            kind="register_script_without_task",
            subject=item.task_name,
            evidence={
                "script": item.script,
                "task_name": item.task_name,
                "task_present": False,
                "feature_active": True,
            },
            action_level=level,
            proposed_action={
                "action": "register_scheduled_task",
                "script": item.script,
                "task": item.task_name,
            },
        ))
    return out


def detect_missing_trigger(
        snapshot: Mapping[str, Any],
        policy_levels: Mapping[str, int] | None = None) -> list[Proposal]:
    """У таска фермы пропал повторяющийся триггер — он не поднимется сам.

    Молчит там, где повтора и не ждали: намеренно отставленные таски и
    on-demand кнопки. «Нет триггера» становится фактом только вместе с
    «триггер ожидался».
    """
    level = resolve_action_level(
        ACTION_TO_POLICY["register_missing_task"],
        policy_levels if policy_levels is not None else {})
    out: list[Proposal] = []
    for task in snapshot.get("tasks", ()):
        if not task.expected_repetition or task.repetition_interval:
            continue
        out.append(Proposal(
            kind="task_missing_repetition",
            subject=task.name,
            evidence={
                "task": task.name,
                "triggers": list(task.triggers),
                "repetition_interval": None,
                "expected_repetition": True,
            },
            action_level=level,
            proposed_action={
                "action": "register_missing_task",
                "task": task.name,
            },
        ))
    return out


def detect_failing_task(
        snapshot: Mapping[str, Any],
        policy_levels: Mapping[str, int] | None = None) -> list[Proposal]:
    """Два ненулевых кода подряд. Один сбой — не тенденция.

    Коды «выполняется» и «ни разу не запускался» падением не считаются: на
    здоровой ферме они массовые (см. NON_FAILURE_RESULTS). Честный отказ
    (`refusal_codes`) показывается, но помечается как отказ — иначе владелец
    получит ложную панику вместо «стенд не прогоняется».
    """
    level = resolve_action_level("diagnostics",
                                 policy_levels if policy_levels is not None else {})
    out: list[Proposal] = []
    for task in snapshot.get("tasks", ()):
        recent = list(task.last_results)[:2]
        if len(recent) < 2:
            continue
        if any(code in NON_FAILURE_RESULTS for code in recent):
            continue
        refusals = set(task.refusal_codes)
        out.append(Proposal(
            kind="task_failing_twice",
            subject=task.name,
            evidence={
                "task": task.name,
                "last_results": recent,
                "all_refusals": bool(refusals) and all(code in refusals for code in recent),
            },
            action_level=level,
            proposed_action={
                "action": "investigate_task_failure",
                "task": task.name,
            },
        ))
    return out
