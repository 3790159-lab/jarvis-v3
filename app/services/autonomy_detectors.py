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
ACTION_TO_POLICY = {"register_scheduled_task": "scheduled_task_mutation"}


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
