# -*- coding: utf-8 -*-
"""Вердикт дрила восстановления — УЛИКА, которую сторож читает вместо прогона.

DEV-46 §9.2: расшифровать клиентский набор умеет только машина с приватным
ключом, то есть ноутбук владельца, — а сторож (`scripts/ops_watchdog.py`)
живёт на хосте. Значит проба не может «запустить дрил и посмотреть на
результат»: она читает файл вердикта, который дрил оставил после себя и
который доехал с ноутбука на хост.

Формат отвечает ровно на три вопроса, и каждый — отдельным полем:

* ``ran_at``  — время ПРОГОНА (epoch-секунды). Возраст вердикта считается по
  нему, а НЕ по mtime файла: файл переезжает между машинами (§9.2 п. 2), и
  mtime переезда — не время проверки. Поле обязано жить ВНУТРИ вердикта
  именно поэтому.
* ``ok``      — сошлись ли величины. Строго ``bool``: строка ``"false"``
  истинна, и вердикт-провал прочитался бы как зелёный.
* ``expected`` / ``actual`` — величины §4.3 (``dialogs``, ``messages``,
  ``last_message_at``): что обещал манифест и что нашлось в восстановленной
  базе. Оба, а не флаг: «restore failed» без чисел не говорит, что чинить
  (§7 п. 6).
* ``detail``  — человеческий хвост от самого дрила.

🔢 ПУТЬ ВЕРДИКТА НАЗВАН В ДВУХ МЕСТАХ, и общей константы у них быть не может:
``scripts/ops_watchdog.py`` — STANDALONE и stdlib-ONLY по построению (он обязан
уметь доложить о смерти всего, что делит с ним окружение, включая ``app/``),
поэтому импортировать этот модуль он не имеет права. Места перечислены
поимённо, чтобы правка одного заставляла найти второе:
  1. ``VERDICT_REL`` здесь                        — куда пишет дрил
  2. ``RESTORE_DRILL_REL`` в ``scripts/ops_watchdog.py`` — откуда читает проба
По той же причине проверка формы существует дважды: `read_verdict` здесь и
разбор в ``_restore_drill_snapshot`` там. Обе обязаны краснеть на одном и том
же наборе поломок.
"""
import json
import os
from pathlib import Path

# Относительно корня дерева. Свой подкаталог, а не общий `state/`: вердикт
# переезжает между машинами отдельным шагом, и его удобнее возить папкой.
VERDICT_REL = "state/backup/restore_drill.json"

# Поля, без которых вердикт — не вердикт. Список ЛИТЕРАЛЬНЫЙ, а не выведенный
# из сигнатуры `write_verdict`: выведенный согласен с реализацией по
# определению и промолчит ровно там, где она забыла поле
# ([[jarvis-literal-lists-not-introspection]]).
REQUIRED_FIELDS = ("ran_at", "ok", "expected", "actual", "detail")


class VerdictError(Exception):
    """Вердикт нечитаем: файла нет, не JSON, или форма битая.

    Отдельный тип, а не голый ValueError: вызывающий обязан отличать «улики
    нет / улика битая» от «улика говорит: провал». Эти два состояния чинятся
    по-разному (DEV-46 §9.4)."""


def write_verdict(path, *, ran_at: float, ok: bool, expected: dict,
                  actual: dict, detail: str) -> Path:
    """Записать вердикт атомарно (`.tmp` + `os.replace`).

    Атомарность здесь не гигиена: файл читает сторож на другой машине, и
    полузаписанный JSON он обязан был бы назвать `unreadable` — то есть
    поднять тревогу о собственной записи, а не о бэкапе.

    UTF-8 и `ensure_ascii=False`: `detail` пишет дрил по-русски, а эскейпы в
    улике, которую читают глазами, — потеря без выигрыша."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ran_at": float(ran_at),
        "ok": bool(ok),
        "expected": dict(expected or {}),
        "actual": dict(actual or {}),
        "detail": str(detail),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_verdict(path) -> dict:
    """Разобрать вердикт С ПРОВЕРКОЙ ФОРМЫ. `VerdictError` на любой поломке.

    Красное по умолчанию: любое сомнение — исключение, а не «сойдёт». Молча
    принятый огрызок вердикта — это зелёная лампа над непроверенным бэкапом.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise VerdictError("вердикта нет: %s" % path)
    except OSError as exc:
        raise VerdictError("вердикт не читается (%s): %s" % (
            type(exc).__name__, path))

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise VerdictError("вердикт не JSON: %s" % exc)

    if not isinstance(data, dict):
        raise VerdictError("вердикт не JSON-объект: %s" % type(data).__name__)

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise VerdictError("в вердикте нет полей: %s" % ", ".join(missing))

    ran_at = data["ran_at"]
    # `bool` исключается явно: `isinstance(True, int)` истинно, и `ran_at:
    # true` проехало бы как «прогон в 1 секунду от эпохи», то есть как
    # предельно устаревший, но ФОРМАЛЬНО ГОДНЫЙ вердикт.
    if isinstance(ran_at, bool) or not isinstance(ran_at, (int, float)):
        raise VerdictError("ran_at не число: %r" % (ran_at,))

    ok = data["ok"]
    # Строго bool. Строка "false" истинна в Python, и провал прочитался бы
    # зелёным — ровно тот способ вранья, ради которого вердикт и заводится.
    if not isinstance(ok, bool):
        raise VerdictError("ok не bool: %r" % (ok,))

    return {
        "ran_at": float(ran_at),
        "ok": ok,
        "expected": data["expected"],
        "actual": data["actual"],
        "detail": data["detail"],
    }
