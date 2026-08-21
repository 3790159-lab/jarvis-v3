# -*- coding: utf-8 -*-
"""DEV-42: у признака «жду человека» ОДНО имя, и его находит ОДИН поиск.

Сторож написан ОТ ФОРМУЛИРОВКИ ДЕФЕКТА (`state/dev_backlog_pipeline.md`,
DEV-42) и от ПУБЛИЧНОГО контракта `chatter/connect/model.py::StepResult`.

## Почему на переименование вообще нужен сторож

Признак стоит дорого: `chatter/connect/__main__.py` решает по нему одному,
печатать `RC_HUMAN` (код 3, «жду тебя») или `RC_CONFLICT` (код 1, «сломано»).
Различие кодов — решение владельца q2, принятое ради того, чтобы человек не
приучался не смотреть на красное.

Пока имён было два (`awaits_human` у параметра, `waits_for_human` у поля),
переход между ними жил в одной строке присваивания, и ни один grep не находил
обе половины сразу: по `waits_for_human` не находились десять мест, где
признак ВЫСТАВЛЯЕТСЯ, а по `awaits_human` — ни объявление поля, ни место, где
по нему принимают решение.

Переименование чинит это ровно один раз. Сторож нужен, чтобы второе имя не
вернулось следующей аркой: возврат ничего не сломает в поведении и потому
пройдёт ревью незамеченным — это и есть класс дефекта.

## Три разных способа поймать возврат, а не три раза один

1. `test_signature_takes_the_field_name` — имя ПАРАМЕТРА совпадает с именем
   ПОЛЯ. Ловит переименование обратно в объявлении.
2. `test_the_old_name_is_gone_from_the_module` — старого имени нет НИГДЕ в
   исходнике пакета. Ловит новое место, где кто-то завёл старое имя заново,
   даже если сигнатура `_open` осталась целой.
3. `test_the_flag_still_reaches_the_field` — признак по-прежнему ДОЕЗЖАЕТ до
   поля. Первые два сторожат имя; без третьего переименование, оборвавшее
   провод (`waits_for_human=False` вместо аргумента), прошло бы зелёным —
   и `RC_HUMAN` молча стал бы `RC_CONFLICT`.

Третий тест — не про имена, и это намеренно: сторож на имя, не проверяющий
поведение, зеленеет ровно тогда, когда переименование сломало смысл.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from chatter.connect import actions
from chatter.connect.model import StepResult, Verdict

#: Имя, под которым признак живёт в публичном контракте `StepResult`.
FIELD_NAME = "waits_for_human"

#: Имя, которое DEV-42 убрал. Литерал, а не вычисление из кода: выведенное имя
#: согласилось бы с реализацией по определению и молчало бы вместе с ней.
RETIRED_NAME = "awaits_human"


def test_signature_takes_the_field_name():
    """Параметр `_open` зовётся так же, как поле, которое он заполняет."""
    params = inspect.signature(actions._open).parameters
    assert FIELD_NAME in params, (
        f"_open() не принимает {FIELD_NAME!r}: параметры {sorted(params)}. "
        f"Признак «жду человека» обязан носить ОДНО имя от вызова до поля"
    )
    assert RETIRED_NAME not in params, (
        f"_open() снова принимает {RETIRED_NAME!r} — второе имя одной вещи "
        f"вернулось, и grep по {FIELD_NAME!r} опять не найдёт места, где "
        f"признак выставляется (DEV-42)"
    )


def test_the_old_name_is_gone_from_the_module():
    """Старого имени нет ни в одном исходнике `chatter/connect`.

    Сигнатуры мало: второе имя может вернуться новой функцией, новым
    параметром или локальной переменной, и `_open` при этом останется целой.
    """
    package_root = Path(actions.__file__).resolve().parent
    offenders = []
    for source in sorted(package_root.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if RETIRED_NAME in line:
                offenders.append(f"{source.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        f"имя {RETIRED_NAME!r} вернулось в chatter/connect — "
        f"{len(offenders)} мест(о):\n  " + "\n  ".join(offenders)
    )


def test_the_flag_still_reaches_the_field():
    """Переименование не оборвало провод: аргумент доезжает до поля.

    Без этого теста сторожа на имена зеленеют и на правке, которая заменила
    аргумент константой, — а это ровно подмена кода 3 кодом 1.
    """
    waiting = actions._open("S9", "нет согласия владельца", "дай согласие",
                            {}, waits_for_human=True)
    assert waiting.waits_for_human is True
    assert waiting.verdict is Verdict.OPEN

    # Умолчание — «это не ожидание человека»: `_open` зовут и там, где факта
    # нет по другой причине, и тогда код обязан остаться 1, а не 3.
    plain = actions._open("S9", "нет согласия владельца", "починить", {})
    assert plain.waits_for_human is False


def test_the_field_name_is_the_one_the_contract_carries():
    """Имя, которое сторожится, — то самое, что несёт `StepResult`.

    Страховка от переименования ОБЕИХ половин разом: если поле однажды
    переедет, этот тест покраснеет раньше, чем сторож начнёт сторожить
    несуществующее имя.
    """
    assert FIELD_NAME in {f for f in StepResult.__dataclass_fields__}, (
        f"StepResult больше не несёт поле {FIELD_NAME!r} — сторож DEV-42 "
        f"сторожит имя, которого нет; проверь контракт §12.7"
    )


@pytest.mark.parametrize("call_site_flag", [True, False])
def test_open_requires_a_todo_when_waiting(call_site_flag):
    """Ожидание человека без «что сделать» остаётся запрещённым (Д11).

    Проверяется вместе с переименованием намеренно: правка трогает ровно тот
    аргумент, от которого зависит это требование `__post_init__`.
    """
    from chatter.connect.model import ConnectContractError

    if call_site_flag:
        with pytest.raises(ConnectContractError):
            actions._open("S9", "почему", "", {}, waits_for_human=True)
    else:
        # Без ожидания человека пустое «что сделать» ловится тем же контрактом
        # для OPEN — здесь важно лишь, что вызов не падает по имени аргумента.
        with pytest.raises(ConnectContractError):
            actions._open("S9", "почему", "", {}, waits_for_human=False)
