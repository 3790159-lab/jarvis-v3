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
    "restart_service": "service_restart",
    "push_branch": "git_push",
}

#: Пороги детекторов. Конфигом, а не константой в теле: «сколько часов
#: незапушенная работа — уже дыра» — это решение владельца, а не факт.
DEFAULT_CONFIG: dict[str, float] = {
    "unpushed_max_age_sec": 12 * 3600.0,
    # Контракт из `scripts/register_healthchecks_ping.ps1`: Period 5 / Grace 10
    # на стороне healthchecks.io => тревога после ~15 минут тишины. Меняешь
    # интервал таска — меняй и это, иначе окна разъедутся.
    "healthchecks_period_sec": 300.0,
    "healthchecks_grace_sec": 600.0,
}


def config_value(config: Mapping[str, Any] | None, key: str) -> float:
    """Значение порога с дефолтом. Нечисло в конфиге игнорируется молча? Нет —
    берём дефолт, но конфиг остаётся объявленным местом решения."""
    value = (config or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_CONFIG[key]
    return float(value)

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

    `temporary=True` означает «скрипт временный по замыслу»: `_TEMP` в имени
    или собственная ветка `-Unregister`. Его таск снят НАМЕРЕННО, и предложить
    «зарегистрируй обратно» значит отменить решение владельца. Причина хранится
    словами: признак `-Unregister` слабее имени и однажды исключит постоянный
    скрипт — пусть это будет видно в снимке, а не выясняется расследованием.
    """

    script: str
    task_name: str | None
    task_present: bool
    feature_active: bool
    inactive_reason: str = ""
    temporary: bool = False
    temporary_reason: str = ""


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


@dataclass(frozen=True)
class ServiceSnapshot:
    """Факты об одном сервисе манифеста на момент снимка.

    `production_enabled=False` — сервис выключен НАМЕРЕННО (в манифесте так у
    `ollama`); это решение владельца, а не дыра.

    `process_present=None` — пробы под сервис нет (docker-контейнеры
    `Get-Process` не показывает). «Не проверяли» ≠ «мёртв»: fail-closed в
    сторону тишины, иначе один прогон родил бы шесть ложных карточек.

    `probe_key` — то, по чему искали (имя скрипта в командной строке или имя
    службы Windows). Факт стабильный, в отличие от PID.
    """

    service_id: str
    owner: str
    entrypoint: str
    criticality: str
    production_enabled: bool
    probe: str
    probe_key: str
    process_present: bool | None


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
        if item.temporary:
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


def detect_manifest_service_without_process(
        snapshot: Mapping[str, Any],
        policy_levels: Mapping[str, int] | None = None) -> list[Proposal]:
    """Манифест объявил сервис рабочим, а процесса под него в снимке нет.

    Молчит в двух разных случаях, и различать их обязательно: сервис выключен
    НАМЕРЕННО (`production_enabled=False`) — решение владельца; пробы под
    сервис нет (`process_present is None`) — мы просто не знаем, а незнание не
    повод будить человека.
    """
    level = resolve_action_level(
        ACTION_TO_POLICY["restart_service"],
        policy_levels if policy_levels is not None else {})
    out: list[Proposal] = []
    for service in snapshot.get("services", ()):
        if not service.production_enabled:
            continue
        if service.process_present is not False:
            continue
        out.append(Proposal(
            kind="manifest_service_without_process",
            subject=service.service_id,
            evidence={
                "service": service.service_id,
                "owner": service.owner,
                "entrypoint": service.entrypoint,
                "criticality": service.criticality,
                "probe": service.probe,
                "probe_key": service.probe_key,
                "process_present": False,
            },
            action_level=level,
            proposed_action={
                "action": "restart_service",
                "service": service.service_id,
                "owner": service.owner,
            },
        ))
    return out


def detect_unpushed_branch(
        snapshot: Mapping[str, Any],
        policy_levels: Mapping[str, int] | None = None,
        config: Mapping[str, Any] | None = None) -> list[Proposal]:
    """Незапушенная работа висит дольше порога — она есть в одной копии.

    Молчит на чистом дереве, на свежем ahead (человек ещё работает) и на ветке
    БЕЗ upstream: там мы не знаем, сколько не уехало, а незнание — не дыра.
    """
    git = snapshot.get("git") or {}
    age = git.get("oldest_unpushed_age_sec")
    if not git.get("upstream") or not git.get("ahead"):
        return []
    if not isinstance(age, (int, float)) or age <= config_value(
            config, "unpushed_max_age_sec"):
        return []
    level = resolve_action_level(
        ACTION_TO_POLICY["push_branch"],
        policy_levels if policy_levels is not None else {})
    hours = round(config_value(config, "unpushed_max_age_sec") / 3600.0, 1)
    return [Proposal(
        kind="branch_unpushed_too_long",
        subject=str(git.get("branch", "")),
        evidence={
            "branch": git.get("branch"),
            "upstream": git.get("upstream"),
            # Возраст и счётчик ahead СПЕЦИАЛЬНО не попадают сюда: оба меняются
            # сами по себе и родили бы новую карточку на каждом прогоне.
            # Тождество наблюдения — самый старый неуехавший коммит.
            "oldest_unpushed_sha": git.get("oldest_unpushed_sha"),
            "threshold_hours": hours,
        },
        action_level=level,
        proposed_action={
            "action": "push_branch",
            "branch": git.get("branch"),
            "upstream": git.get("upstream"),
        },
    )]


def detect_stale_healthcheck(
        snapshot: Mapping[str, Any],
        policy_levels: Mapping[str, int] | None = None,
        config: Mapping[str, Any] | None = None) -> list[Proposal]:
    """Метка пинга старше окна `period + grace` — дед-ман умер молча.

    Молчит, когда метки нет вовсе («пинг не настроен» — другой класс) и когда
    возраст отрицательный (метка в БУДУЩЕМ — баг эпохи +3ч, пойманный 31.07;
    «старой» она от этого не становится).
    """
    age = (snapshot.get("stamps") or {}).get("healthchecks_age_sec")
    threshold = (config_value(config, "healthchecks_period_sec")
                 + config_value(config, "healthchecks_grace_sec"))
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return []
    # Метка в БУДУЩЕМ (баг эпохи +3ч, 31.07) молчит сама по себе: сравнение с
    # порогом её отсекает. Отдельная проверка `age < 0` здесь БЫЛА и оказалась
    # мёртвой — мутация её снятия выживала. Держит поведение тест, а не ветка.
    if age <= threshold:
        return []
    level = resolve_action_level(
        "diagnostics", policy_levels if policy_levels is not None else {})
    return [Proposal(
        kind="healthcheck_stamp_stale",
        subject="healthchecks",
        evidence={
            # Возраст растёт каждую секунду — в наблюдение он не попадает,
            # иначе карточка пересоздавалась бы на каждом прогоне.
            "stamp": "state/healthchecks_last.txt",
            "stamp_present": True,
            "threshold_minutes": round(threshold / 60.0, 1),
        },
        action_level=level,
        proposed_action={
            "action": "investigate_healthcheck_ping",
            "task": "JarvisHealthchecksPing",
        },
    )]


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
