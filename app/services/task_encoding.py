# -*- coding: utf-8 -*-
"""DEV-58: сверка защиты кодировки у задач Планировщика.

Задача Планировщика, запускающая python, пишет диагностику в stdout, который
Планировщик НЕ сохраняет. Причину отказа читают, воспроизводя прогон руками, —
и если вывод при этом рвётся консольной кодировкой, причина теряется ВТОРОЙ
раз, ровно в аварии. За сутки 23.08 это случилось дважды: код возврата 2 у
ночного дрила без единого слова о причине, и утраченная соль в DEV-57.

Лечение — `-X utf8` в аргументах действия задачи. НЕ обёртка: прослойка умеет
проглотить код возврата и потерять аргументы. НЕ переменная окружения: она
действует незаметно и на всё сразу, включая ручные прогоны.

ЗДЕСЬ ЖИВЁТ ТОЛЬКО СВЕРКА и ничего больше. Опрос живой системы — отдельно и
снаружи: снимок ВНЕДРЯЕТСЯ. Сверка, ходящая в планировщик сама, проверялась бы
только на машине с этим планировщиком, то есть была бы стендом, а не сторожем.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

# ── Ожидание. ЛИТЕРАЛЬНОЕ и в ОБЕ стороны ───────────────────────────────────
#
# Список не выводится из системы намеренно: выведенный согласен с системой по
# определению и промолчит ровно там, где задачу завели и забыли. Поэтому
# сверка идёт в обе стороны, и `unexpected` — не побочный случай, а главный:
# это та самая завтрашняя задача.
PROTECTION_X_UTF8 = "x_utf8"
PROTECTION_EXEMPT = "exempt"

TASK_ENCODING_EXPECTATION: dict = {
    "JarvisStateBackup": PROTECTION_X_UTF8,
    "JarvisRestoreDrill": PROTECTION_X_UTF8,
    "JarvisChatterCacheDigest": PROTECTION_X_UTF8,
    "JarvisDrillNightly": PROTECTION_X_UTF8,
    "JarvisIgTokenRefresh": PROTECTION_EXEMPT,
}

# Причина исключения — МАШИНОЧИТАЕМАЯ и отдельным словарём.
#
# Отдельным, а не полем в таблице выше: таблица обязана остаться простым
# отображением «имя -> защита», иначе её нельзя прочитать одним взглядом. А
# исключение БЕЗ причины через месяц неотличимо от потерянной строки — и тогда
# следующий читатель не узнает, забыли её или вынесли осознанно.
EXEMPT_REASONS: dict = {
    "JarvisIgTokenRefresh":
        "решение владельца 24.08: вне арки DEV-58, разбирается отдельно",
}

# ── Состояния. ПЯТЬ, и ни одно не склеивается с другим ──────────────────────
STATE_OK = "ok"                    # ожидается защищённой и защищена
STATE_UNPROTECTED = "unprotected"  # ожидается защищённой, но `-X utf8` нет
STATE_EXEMPT = "exempt"            # намеренно вне арки
STATE_MISSING = "missing"          # есть в ожидании, в системе НЕТ
STATE_UNEXPECTED = "unexpected"    # есть в системе, в ожидании НЕТ

# `missing` и `unexpected` — РАЗНЫЕ вещи с разными действиями: первое чинится
# разбором, куда делась задача, второе — дописыванием строки в ожидание.
# Склеить их в одно «расхождение» значит потерять ответ на вопрос «что делать».


@dataclass(frozen=True)
class TaskVerdict:
    """Вердикт по одной задаче.

    `reason` присутствует ВСЕГДА, в том числе на зелёном, — как у проб в
    `ops_watchdog`. Иначе состояния неразличимы машинно и дедуп по причине не
    увидит смены красного на другое красное.
    """
    task: str
    state: str
    reason: str
    detail: str = ""


# Отрезаем закавыченные куски ПЕРЕД поиском: путь вида
# "C:\что-то\-X utf8\run.py" не является защитой, хотя подстрока в нём есть.
_QUOTED = re.compile(r'"[^"]*"')

# Границы с ОБЕИХ сторон: слева нельзя лишний дефис (`--X utf8`), справа —
# лишняя буква (`-X utf8x`). Пробелов между `-X` и `utf8` может быть сколько
# угодно: это оформление, а не смысл.
_X_UTF8 = re.compile(r"(?<![\w-])-X\s+utf8(?![\w])")


def has_x_utf8(arguments: str) -> bool:
    """Несёт ли строка аргументов настоящий `-X utf8`."""
    return bool(_X_UTF8.search(_QUOTED.sub(" ", arguments or "")))


def _read_record(record: Any) -> tuple[Optional[str], str]:
    """(имя задачи, строка аргументов) из записи снимка.

    Форма записи намеренно НЕ одна: сборщик снимка живёт снаружи и может
    отдавать что угодно разумное. Жёсткая форма здесь означала бы, что «не
    понял запись» выглядит как «задачи нет», а это ровно та склейка, которой
    вся сверка и избегает.
    """
    if isinstance(record, Mapping):
        name = record.get("name") or record.get("task") or record.get("TaskName")
        args = (record.get("arguments") or record.get("args")
                or record.get("Arguments") or "")
        return (str(name) if name is not None else None), str(args)

    name = getattr(record, "name", None) or getattr(record, "task", None)
    if name is not None:
        args = (getattr(record, "arguments", None)
                or getattr(record, "args", None) or "")
        return str(name), str(args)

    if isinstance(record, (tuple, list)) and len(record) >= 2:
        return str(record[0]), str(record[1])

    return None, ""


def compare_tasks(snapshot: Iterable[Any],
                  *,
                  expectation: Optional[Mapping] = None,
                  exempt_reasons: Optional[Mapping] = None,
                  ) -> list:
    """Сверить СНИМОК живых задач с литеральным ожиданием.

    Возвращает список `TaskVerdict` — по одному на каждое имя, встреченное
    хотя бы с одной стороны. Порядок: сначала ожидаемые (в порядке ожидания),
    затем неожиданные (в порядке снимка), чтобы вывод был устойчив.

    В планировщик НЕ ходит: снимок приходит снаружи.
    """
    expectation = (TASK_ENCODING_EXPECTATION if expectation is None
                   else expectation)
    exempt_reasons = (EXEMPT_REASONS if exempt_reasons is None
                      else exempt_reasons)

    seen: dict = {}
    order: list = []
    for record in snapshot or ():
        name, args = _read_record(record)
        if name is None:
            continue
        if name not in seen:
            order.append(name)
        seen[name] = args

    out: list = []

    for name, protection in expectation.items():
        # ИСКЛЮЧЕНИЕ СИЛЬНЕЕ ОТСУТСТВИЯ, и это решение, а не мелочь.
        #
        # Соблазн обратный: «задача исчезла — скажи об этом, даже если чинить
        # её мы не собирались». Но предмет ЭТОГО сторожа — кодировка. Скажи он
        # `missing` про исключённую, и лампа загорится по причине, к кодировке
        # отношения не имеющей: владельцу придётся либо чинить вне арки, либо
        # гасить сигнал. «Красное при полном порядке» приучает не читать
        # красное — а это ровно то, ради чего сторож и заводился.
        #
        # Граница, которую надо знать вслух: удалённая ИСКЛЮЧЁННАЯ задача этим
        # сторожем не видна. Её существование обязан стеречь тот, кто за неё
        # отвечает, а не сверка кодировки.
        if protection == PROTECTION_EXEMPT:
            why = exempt_reasons.get(name, "причина исключения не записана")
            out.append(TaskVerdict(
                task=name, state=STATE_EXEMPT, reason=STATE_EXEMPT,
                detail=why))
            continue

        if name not in seen:
            out.append(TaskVerdict(
                task=name, state=STATE_MISSING,
                reason=STATE_MISSING,
                detail="задача есть в ожидании, но в системе её нет"))
            continue

        if has_x_utf8(seen[name]):
            out.append(TaskVerdict(
                task=name, state=STATE_OK, reason=STATE_OK,
                detail="`-X utf8` на месте"))
        else:
            out.append(TaskVerdict(
                task=name, state=STATE_UNPROTECTED, reason=STATE_UNPROTECTED,
                detail="в аргументах нет `-X utf8`: диагностика этой задачи "
                       "порвётся кодировкой ровно тогда, когда её читают"))

    for name in order:
        if name in expectation:
            continue
        out.append(TaskVerdict(
            task=name, state=STATE_UNEXPECTED, reason=STATE_UNEXPECTED,
            detail="задача есть в системе, но её нет в ожидании — "
                   "завели и забыли про кодировку"))

    return out
