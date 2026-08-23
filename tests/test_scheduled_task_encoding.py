# -*- coding: utf-8 -*-
"""Сторожа DEV-58: python-задачи Планировщика обязаны нести `-X utf8`.

ЗАЧЕМ. Задача Планировщика пишет диагностику в stdout, который Планировщик не
сохраняет. Когда причину отказа идут смотреть руками, вывод рвётся кодировкой
консоли (cp866/cp1251) — и причина теряется ВТОРОЙ раз, ровно в аварии.
Лечение — `-X utf8` в аргументах действия задачи: не обёртка `.bat` (прослойка
глотает код возврата и теряет аргументы) и не переменная окружения (действует
незаметно и на всё сразу).

Написаны ОТ КОНТРАКТА (задание DEV-58, пункты 1-3 и обязательные пины 1-9),
ДО и БЕЗ просмотра реализации: иначе тест и код унаследуют одно неверное
допущение.

Все ожидаемые значения — ЛИТЕРАЛЬНЫЕ. Выведенный из кода список согласен с
кодом по определению и молчит там, где код забыл.

Чистота: настоящий Планировщик здесь не опрашивается, сети и subprocess нет.
Снимок живых задач — подставной, он внедряется в сверку параметром.

ИМЕНА. Контракт задал СМЫСЛ (таблица ожидания; чистая функция сверки; пять
несклеиваемых состояний), но НЕ задал ни имени таблицы, ни имени функции, ни
формы записи снимка, ни формы результата. Угадывать нельзя: зелёный тест на
выдуманное имя — ложный зелёный, а красный на выдуманное имя обвиняет
невиновного. Поэтому и таблица, и функция РАЗЫСКИВАЮТСЯ ПО КОНТРАКТУ (как
сборщик в DEV-52), а если разыскать не удалось — тест падает ГРОМКО и говорит,
что именно контракт обязан дозадать. «Не смогли проверить» не имеет права
выглядеть как «проверили, всё хорошо».
"""
from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import task_encoding as mod


# ─────────────────────────────────────────────────────────────────────────────
# Литеральные ожидания. Не выводить из кода.
# ─────────────────────────────────────────────────────────────────────────────

# Контракт §1 — таблица «имя задачи -> требуемая защита», дословно.
EXPECTED_TABLE = {
    "JarvisStateBackup": "x_utf8",
    "JarvisRestoreDrill": "x_utf8",
    "JarvisChatterCacheDigest": "x_utf8",
    "JarvisDrillNightly": "x_utf8",
    "JarvisIgTokenRefresh": "exempt",
}

# Контракт §1 — допустимые значения защиты.
PROTECTION_X_UTF8 = "x_utf8"
PROTECTION_EXEMPT = "exempt"
PROTECTIONS = {PROTECTION_X_UTF8, PROTECTION_EXEMPT}

# Контракт §3 — пять состояний сверки. Склеивать нельзя.
STATUS_OK = "ok"
STATUS_UNPROTECTED = "unprotected"
STATUS_EXEMPT = "exempt"
STATUS_MISSING = "missing"
STATUS_UNEXPECTED = "unexpected"
STATUSES = {STATUS_OK, STATUS_UNPROTECTED, STATUS_EXEMPT,
            STATUS_MISSING, STATUS_UNEXPECTED}

# Строки аргументов. Ровно те оформления, что перечислены в пине 8.
ARGS_ONE_SPACE = r'-X utf8 C:\jarvis\scripts\state_backup.py'
ARGS_MANY_SPACES = r'-X    utf8    C:\jarvis\scripts\state_backup.py'
ARGS_NO_FLAG = r'C:\jarvis\scripts\state_backup.py'
ARGS_UTF8X = r'-X utf8x C:\jarvis\scripts\state_backup.py'
ARGS_DOUBLE_DASH = r'--X utf8 C:\jarvis\scripts\state_backup.py'
ARGS_INSIDE_QUOTED_PATH = r'"C:\jarvis\odd -X utf8 folder\state_backup.py"'

# Задача, которой в ожидании НЕТ: та самая «завели и забыли».
FORGOTTEN_TASK = "JarvisNightlyDigestNew"
FORGOTTEN_ARGS = r'-X utf8 C:\jarvis\scripts\nightly_digest_new.py'


# ─────────────────────────────────────────────────────────────────────────────
# Разбор значений. Форма значений контрактом не задана, поэтому из значения
# ВЫТАСКИВАЕТСЯ текст, а сверяется он с ЛИТЕРАЛАМИ выше.
# ─────────────────────────────────────────────────────────────────────────────

def _texts(value, depth: int = 2) -> list:
    """Все строки, которые несёт значение: сама строка, `.value`/`.name` у
    Enum, значения словаря/полей объекта/элементов последовательности."""
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, Enum):
        out = [value.name.lower()]
        if isinstance(value.value, str):
            out.append(value.value.strip())
        return [t for t in out if t]
    if depth <= 0:
        return []
    out = []
    if isinstance(value, Mapping):
        for v in value.values():
            out.extend(_texts(v, depth - 1))
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            out.extend(_texts(v, depth - 1))
        return out
    fields = getattr(value, "__dict__", None)
    if isinstance(fields, dict):
        for v in fields.values():
            out.extend(_texts(v, depth - 1))
    return out


def _pick(value, allowed: set):
    """Единственный литерал из `allowed`, который несёт значение. Ноль или
    больше одного — None: догадка тут хуже отказа."""
    hits = {t for t in _texts(value) if t in allowed}
    if len(hits) == 1:
        return hits.pop()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Розыск таблицы ожидания. Имя контрактом не задано.
# ─────────────────────────────────────────────────────────────────────────────

def _table_candidates() -> list:
    out = []
    for name, obj in sorted(vars(mod).items()):
        if name.startswith("__"):
            continue
        if not isinstance(obj, Mapping) or not obj:
            continue
        if not all(isinstance(k, str) for k in obj):
            continue
        pairs = {k: _pick(v, PROTECTIONS) for k, v in obj.items()}
        if any(v is None for v in pairs.values()):
            continue
        out.append((name, obj, pairs))
    return out


def _expectation_table():
    """(имя атрибута, сам объект, {имя задачи: защита}) или громкий отказ."""
    candidates = _table_candidates()
    if not candidates:
        pytest.fail(
            "в app.services.task_encoding нет таблицы ожидания «имя задачи -> "
            "защита» (контракт §1): не нашлось ни одного словаря уровня модуля, "
            "все значения которого сводятся к литералам %r. Контракт обязан "
            "дозадать точное ИМЯ таблицы и ФОРМУ значения. Публичные имена "
            "модуля сейчас: %s"
            % (sorted(PROTECTIONS),
               ", ".join(sorted(n for n in vars(mod) if not n.startswith("_")))))
    if len(candidates) > 1:
        pytest.fail(
            "в app.services.task_encoding НЕСКОЛЬКО словарей подходят под "
            "таблицу ожидания (%s) — какой из них ожидание, контракт не "
            "говорит; разыскивать вслепую нельзя, имя надо дозадать"
            % ", ".join(n for n, _, _ in candidates))
    return candidates[0]


# ─────────────────────────────────────────────────────────────────────────────
# Розыск функции сверки и формы снимка. Контрактом не заданы ни имя функции,
# ни форма записи снимка, ни форма результата.
# ─────────────────────────────────────────────────────────────────────────────

def _enc_pair(name: str, args: str):
    return (name, args)


def _enc_mapping(name: str, args: str):
    return {"name": name, "task_name": name,
            "arguments": args, "args": args, "argument": args}


def _enc_object(name: str, args: str):
    return SimpleNamespace(name=name, task_name=name,
                           arguments=args, args=args, argument=args)


def _class_encoders() -> list:
    """Классы-записи самого модуля: `Cls(имя, аргументы)`."""
    out = []
    for name, obj in sorted(vars(mod).items()):
        if name.startswith("_") or not inspect.isclass(obj):
            continue
        if getattr(obj, "__module__", None) != mod.__name__:
            continue
        try:
            params = [p for p in inspect.signature(obj).parameters.values()
                      if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                    inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        except (TypeError, ValueError):
            continue
        if len(params) != 2:
            continue
        out.append(("%s(name, args)" % name,
                    lambda n, a, cls=obj: cls(n, a)))
    return out


def _encoders() -> list:
    return _class_encoders() + [
        ("tuple(name, args)", _enc_pair),
        ("dict(name=..., arguments=...)", _enc_mapping),
        ("object(.name, .arguments)", _enc_object),
    ]


_NAME_KEYS = ("name", "task_name", "task", "taskname", "title")


def _record_name(rec):
    if isinstance(rec, str):
        return rec.strip()
    for key in _NAME_KEYS:
        if isinstance(rec, Mapping) and isinstance(rec.get(key), str):
            return rec[key].strip()
        val = getattr(rec, key, None)
        if isinstance(val, str):
            return val.strip()
    if isinstance(rec, (list, tuple)):
        plain = [x.strip() for x in rec
                 if isinstance(x, str) and x.strip() not in STATUSES]
        if len(plain) == 1:
            return plain[0]
        jarvis = [x for x in plain if x.startswith("Jarvis")]
        if len(jarvis) == 1:
            return jarvis[0]
    return None


def _statuses(result, depth: int = 3):
    """{имя задачи: состояние} из результата сверки, чья форма не задана."""
    if depth <= 0 or result is None or isinstance(result, str):
        return None

    if isinstance(result, Mapping):
        keys = {k.strip() for k in result if isinstance(k, str)}
        # Форма «сгруппировано по состоянию»: {состояние: [задачи]}.
        if keys and keys <= STATUSES and all(
                isinstance(v, (list, tuple, set, frozenset))
                for v in result.values()):
            out = {}
            for status, bucket in result.items():
                for rec in bucket:
                    name = _record_name(rec)
                    if name is None:
                        return None
                    out[name] = status.strip()
            return out or None
        # Форма «имя задачи -> состояние (или запись с состоянием)».
        out = {}
        for key, val in result.items():
            if not isinstance(key, str):
                return None
            status = _pick(val, STATUSES)
            if status is None:
                out = None
                break
            out[key.strip()] = status
        if out:
            return out

    if isinstance(result, (list, tuple, set, frozenset)):
        out = {}
        for rec in result:
            name = _record_name(rec)
            status = _pick(rec, STATUSES)
            if name is None or status is None:
                out = None
                break
            out[name] = status
        if out:
            return out
        # Кортеж «(строки, сводка)» и т.п.: искать состояния в элементах.
        for item in result:
            got = _statuses(item, depth - 1)
            if got:
                return got
        return None

    fields = getattr(result, "__dict__", None)
    if isinstance(fields, dict):
        for key in ("rows", "entries", "results", "records", "items",
                    "tasks", "checks", "by_name", "statuses"):
            if key in fields:
                got = _statuses(fields[key], depth - 1)
                if got:
                    return got
        for val in fields.values():
            got = _statuses(val, depth - 1)
            if got:
                return got
    return None


def _checker_candidates() -> list:
    out = []
    for name, obj in sorted(vars(mod).items()):
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if getattr(obj, "__module__", None) != mod.__name__:
            continue
        try:
            params = list(inspect.signature(obj).parameters.values())
        except (TypeError, ValueError):
            continue
        if not params:
            continue
        first = params[0]
        if first.kind in (inspect.Parameter.VAR_POSITIONAL,
                          inspect.Parameter.VAR_KEYWORD):
            continue
        out.append((name, obj, first))
    return out


def _invoke(fn, first, snapshot, table):
    """Позвать функцию сверки снимком; при нужде — вторым доводом ожиданием."""
    calls = []
    if first.kind is inspect.Parameter.KEYWORD_ONLY:
        calls.append(lambda: fn(**{first.name: snapshot}))
    else:
        calls.append(lambda: fn(snapshot))
        calls.append(lambda: fn(snapshot, table))
    last = None
    for call in calls:
        try:
            return call(), None
        except TypeError as exc:  # не та сигнатура — пробуем следующую форму
            last = exc
        except Exception as exc:  # noqa: BLE001 — отказ разбираем выше
            return None, exc
    return None, last


# Контрольный снимок для розыска: одна ЗАЩИЩЁННАЯ задача из ожидания.
# Форма снимка принимается только если сверка эту запись РЕАЛЬНО увидела,
# то есть состояние вышло не `missing`. Иначе «не понял снимок» выглядело бы
# как «задачи нет» — та самая склейка, которую контракт запрещает.
_CONTROL = [("JarvisStateBackup", ARGS_ONE_SPACE)]

_SEAM = []


def _seam():
    """(имя функции, функция, первый параметр, имя формы, кодировщик)."""
    if _SEAM:
        return _SEAM[0]
    _, table_obj, _ = _expectation_table()
    checkers = _checker_candidates()
    if not checkers:
        pytest.fail(
            "в app.services.task_encoding нет публичной функции сверки "
            "(контракт §2: снимок живых задач внутрь, результат сверки "
            "наружу). Контракт обязан дозадать ИМЯ функции и ИМЯ параметра, "
            "которым внедряется снимок. Публичные имена модуля сейчас: %s"
            % ", ".join(sorted(n for n in vars(mod) if not n.startswith("_"))))

    tried = []
    for fn_name, fn, first in checkers:
        for enc_name, enc in _encoders():
            snapshot = [enc(n, a) for n, a in _CONTROL]
            result, exc = _invoke(fn, first, snapshot, table_obj)
            if exc is not None:
                tried.append("%s + %s: %r" % (fn_name, enc_name, exc))
                continue
            got = _statuses(result)
            if not got:
                tried.append("%s + %s: результат %r не разбирается на "
                             "состояния" % (fn_name, enc_name, type(result)))
                continue
            status = got.get("JarvisStateBackup")
            if status is None:
                tried.append("%s + %s: в результате нет JarvisStateBackup"
                             % (fn_name, enc_name))
                continue
            if status == STATUS_MISSING:
                tried.append("%s + %s: снимок не прочитан (задача из снимка "
                             "вышла %r)" % (fn_name, enc_name, STATUS_MISSING))
                continue
            _SEAM.append((fn_name, fn, first, enc_name, enc))
            return _SEAM[0]

    pytest.fail(
        "не удалось позвать сверку: ни одна публичная функция модуля не "
        "приняла подставной снимок ни в одной из известных форм записи "
        "(класс модуля с двумя доводами, кортеж, словарь, объект с полями). "
        "Контракт §2 не задал ФОРМУ записи снимка — её надо дозадать. "
        "Попытки:\n  %s" % "\n  ".join(tried))


def _compare(pairs) -> dict:
    """Сверить подставной снимок и вернуть {имя задачи: состояние}."""
    fn_name, fn, first, enc_name, enc = _seam()
    _, table_obj, _ = _expectation_table()
    snapshot = [enc(name, args) for name, args in pairs]
    result, exc = _invoke(fn, first, snapshot, table_obj)
    if exc is not None:
        pytest.fail("сверка %s выпустила наружу %r на снимке %r"
                    % (fn_name, exc, pairs))
    got = _statuses(result)
    if not got:
        pytest.fail("результат сверки %s (%r) не разбирается на состояния "
                    "контракта §3 %s" % (fn_name, result, sorted(STATUSES)))
    unknown = sorted(set(got.values()) - STATUSES)
    if unknown:
        pytest.fail("сверка %s вернула состояния вне контракта §3: %s "
                    "(разрешены только %s)"
                    % (fn_name, unknown, sorted(STATUSES)))
    return got


def _status_of(pairs, task: str) -> str:
    got = _compare(pairs)
    if task not in got:
        pytest.fail("в результате сверки нет задачи %r; результат: %r"
                    % (task, got))
    return got[task]


def _all_protected_snapshot() -> list:
    """Снимок, где все задачи-`x_utf8` защищены: фон без нарушений."""
    return [(name, ARGS_ONE_SPACE)
            for name, prot in EXPECTED_TABLE.items()
            if prot == PROTECTION_X_UTF8]


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 1 — литеральный пин на саму таблицу ожидания, сверка в ОБЕ стороны.
# ─────────────────────────────────────────────────────────────────────────────

def test_expectation_table_is_the_literal_five_names():
    attr, _, pairs = _expectation_table()
    assert pairs == EXPECTED_TABLE, (
        "таблица ожидания %s разошлась с контрактом §1.\n"
        "  забыты в модуле: %s\n"
        "  лишние в модуле: %s\n"
        "  разошлась защита: %s\n"
        "Ожидание не выводится из Планировщика: выведенный список согласен с "
        "Планировщиком по определению и промолчит там, где задачу забыли."
        % (attr,
           sorted(set(EXPECTED_TABLE) - set(pairs)),
           sorted(set(pairs) - set(EXPECTED_TABLE)),
           sorted((n, EXPECTED_TABLE[n], pairs[n])
                  for n in set(pairs) & set(EXPECTED_TABLE)
                  if pairs[n] != EXPECTED_TABLE[n])))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 2 — защищённая задача -> ok.
# ─────────────────────────────────────────────────────────────────────────────

def test_protected_task_is_ok():
    got = _status_of([("JarvisStateBackup", ARGS_ONE_SPACE)],
                     "JarvisStateBackup")
    assert got == STATUS_OK, (
        "задача JarvisStateBackup несёт `-X utf8`, а сверка сказала %r "
        "вместо %r" % (got, STATUS_OK))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 3 — задача без `-X utf8` -> unprotected (не ok и не missing).
# ─────────────────────────────────────────────────────────────────────────────

def test_task_without_x_utf8_is_unprotected_not_ok_not_missing():
    got = _status_of([("JarvisStateBackup", ARGS_NO_FLAG)],
                     "JarvisStateBackup")
    assert got != STATUS_OK, (
        "задача JarvisStateBackup БЕЗ `-X utf8` названа %r: незащищённая "
        "задача выдана за здоровую — вывод порвётся ровно в аварии"
        % STATUS_OK)
    assert got != STATUS_MISSING, (
        "задача JarvisStateBackup ЕСТЬ в снимке, но названа %r: «задачи нет» "
        "и «задача без защиты» — разные вещи с разными действиями"
        % STATUS_MISSING)
    assert got == STATUS_UNPROTECTED, (
        "ожидалось %r, сверка сказала %r" % (STATUS_UNPROTECTED, got))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 4 — exempt-задача без `-X utf8` -> exempt, и это НЕ нарушение.
# ─────────────────────────────────────────────────────────────────────────────

def test_exempt_task_without_x_utf8_is_exempt_and_not_a_violation():
    snapshot = _all_protected_snapshot() + [
        ("JarvisIgTokenRefresh", ARGS_NO_FLAG)]
    got = _compare(snapshot)
    assert got.get("JarvisIgTokenRefresh") == STATUS_EXEMPT, (
        "JarvisIgTokenRefresh намеренно вне арки (решение владельца 24.08), "
        "но сверка сказала %r вместо %r"
        % (got.get("JarvisIgTokenRefresh"), STATUS_EXEMPT))
    violators = sorted(n for n, s in got.items() if s == STATUS_UNPROTECTED)
    assert violators == [], (
        "в снимке нарушителей нет (все задачи-x_utf8 защищены, единственная "
        "без `-X utf8` — намеренно исключённая), а сверка назвала "
        "нарушителями %s: исключение перестало быть исключением" % violators)


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 5 — задача из ожидания, которой нет в снимке -> missing.
# ─────────────────────────────────────────────────────────────────────────────

def test_expected_task_absent_from_snapshot_is_missing():
    snapshot = [(name, args) for name, args in _all_protected_snapshot()
                if name != "JarvisRestoreDrill"]
    got = _status_of(snapshot, "JarvisRestoreDrill")
    assert got == STATUS_MISSING, (
        "JarvisRestoreDrill есть в ожидании и НЕТ в снимке — это %r, а сверка "
        "сказала %r" % (STATUS_MISSING, got))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 6 — задача в снимке, которой нет в ожидании -> unexpected.
# ─────────────────────────────────────────────────────────────────────────────

def test_snapshot_task_absent_from_expectation_is_unexpected():
    snapshot = _all_protected_snapshot() + [(FORGOTTEN_TASK, FORGOTTEN_ARGS)]
    got = _status_of(snapshot, FORGOTTEN_TASK)
    assert got == STATUS_UNEXPECTED, (
        "%s есть в системе и НЕТ в ожидании — это %r, а сверка сказала %r. "
        "Ради этого состояния сверка и идёт в ОБЕ стороны: это и есть та "
        "задача, которую завели и забыли"
        % (FORGOTTEN_TASK, STATUS_UNEXPECTED, got))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 7 — missing и unexpected не склеиваются: один и тот же факт «имя есть
# только с одной стороны» даёт РАЗНЫЕ состояния по сторонам.
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_and_unexpected_are_different_verdicts():
    snapshot = [(name, args) for name, args in _all_protected_snapshot()
                if name != "JarvisDrillNightly"]
    snapshot.append((FORGOTTEN_TASK, FORGOTTEN_ARGS))
    got = _compare(snapshot)
    only_in_expectation = got.get("JarvisDrillNightly")
    only_in_snapshot = got.get(FORGOTTEN_TASK)
    assert only_in_expectation == STATUS_MISSING, (
        "имя есть только в ОЖИДАНИИ -> %r, сверка сказала %r"
        % (STATUS_MISSING, only_in_expectation))
    assert only_in_snapshot == STATUS_UNEXPECTED, (
        "имя есть только в СНИМКЕ -> %r, сверка сказала %r"
        % (STATUS_UNEXPECTED, only_in_snapshot))
    assert only_in_expectation != only_in_snapshot, (
        "обе стороны склеены в одно состояние %r: «задача пропала» и «задачу "
        "завели и забыли» требуют РАЗНЫХ действий" % only_in_expectation)


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 8 — распознавание `-X utf8` устойчиво к оформлению и не ловится на
# похожее.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case, args, expected", [
    ("один пробел", ARGS_ONE_SPACE, STATUS_OK),
    ("несколько пробелов", ARGS_MANY_SPACES, STATUS_OK),
    ("похожий флаг -X utf8x", ARGS_UTF8X, STATUS_UNPROTECTED),
    ("похожий флаг --X utf8", ARGS_DOUBLE_DASH, STATUS_UNPROTECTED),
    ("подстрока внутри пути в кавычках", ARGS_INSIDE_QUOTED_PATH,
     STATUS_UNPROTECTED),
])
def test_x_utf8_recognition_is_robust_to_formatting(case, args, expected):
    got = _status_of([("JarvisStateBackup", args)], "JarvisStateBackup")
    assert got == expected, (
        "оформление аргументов «%s» (%r): ожидалось %r, сверка сказала %r"
        % (case, args, expected, got))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 9 — сверка не обращается к Планировщику. Статический пин по исходнику.
#
# Через `ast`, как велит контракт: строки в позиции ДОКСТРОКИ не считаются
# обращением — модулю позволено объяснить словами, что опрос Планировщика
# живёт отдельно. Всё остальное — литералы, имена, атрибуты, импорты —
# проверяется целиком.
# ─────────────────────────────────────────────────────────────────────────────

FORBIDDEN_TOKENS = ("get-scheduledtask", "schtasks", "win32com")


def _module_source() -> tuple:
    path = Path(mod.__file__)
    return path, path.read_text(encoding="utf-8")


def _module_level_nodes(tree) -> list:
    """Узлы, исполняемые ПРИ ИМПОРТЕ модуля (в т.ч. внутри верхних if/try)."""
    out = []
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, (ast.If, ast.Try)):
            stack.extend(node.body)
            stack.extend(node.orelse)
            stack.extend(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
    return out


def _docstring_ids(tree) -> set:
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        head = body[0]
        if (isinstance(head, ast.Expr)
                and isinstance(head.value, ast.Constant)
                and isinstance(head.value.value, str)):
            out.add(id(head.value))
    return out


def test_comparison_never_reaches_the_scheduler():
    path, src = _module_source()
    tree = ast.parse(src)

    imported = set()
    for node in _module_level_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "subprocess" not in imported, (
        "%s импортирует subprocess на уровне модуля: сверка обязана быть "
        "чистой, опрос Планировщика живёт отдельно (контракт §2)" % path)

    docstrings = _docstring_ids(tree)
    hits = []
    for node in ast.walk(tree):
        words = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            words.append(node.value)
        elif isinstance(node, ast.Name):
            words.append(node.id)
        elif isinstance(node, ast.Attribute):
            words.append(node.attr)
        elif isinstance(node, ast.alias):
            words.append(node.name)
            if node.asname:
                words.append(node.asname)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            words.append(node.name)
        for word in words:
            low = word.lower()
            for token in FORBIDDEN_TOKENS:
                if token in low:
                    hits.append((getattr(node, "lineno", "?"), token, word))
    assert hits == [], (
        "%s обращается к Планировщику из слоя сверки: %s. Снимок обязан "
        "внедряться, а не сниматься внутри — иначе сверку нельзя прогнать "
        "ни на подставных данных, ни на чужой машине" % (path, hits))
