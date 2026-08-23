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


# ═════════════════════════════════════════════════════════════════════════════
# ШОВ ВНЕДРЕНИЯ (дозадано контрактом 24.08).
#
# Функция сверки принимает ожидание ПАРАМЕТРОМ (умолчание None -> модульная
# таблица), и то же самое — словарём ПРИЧИН исключения. Смысл шва: сверку
# обязано быть можно прогнать на ВЫДУМАННОМ наборе задач, не завися от
# сегодняшнего состава настоящих.
#
# Пины 1-9 все до одного работают на НАСТОЯЩЕЙ таблице модуля, поэтому шов
# ими не покрыт: реализация, которая параметр молча игнорирует, проходит их
# все до единого.
#
# ИМЕНА параметров контрактом не заданы, поэтому они РАЗЫСКИВАЮТСЯ по
# поведению (как разысканы таблица и функция): среди именованных параметров с
# умолчанием None ищется тот, на который сверка РЕАГИРУЕТ. Розыск по поведению
# заодно и есть проверка: параметр, который игнорируется, не отзовётся ни на
# одном имени — и розыск упадёт ГРОМКО, назвав, что дозадать.
# ═════════════════════════════════════════════════════════════════════════════

# Выдуманный набор задач: этих имён в настоящей таблице НЕТ и быть не должно.
FAKE_PROTECTED_TASK = "ZzGuardFakeProtectedTask"
FAKE_EXEMPT_TASK = "ZzGuardFakeExemptTask"
FAKE_TABLE = {
    FAKE_PROTECTED_TASK: PROTECTION_X_UTF8,
    FAKE_EXEMPT_TASK: PROTECTION_EXEMPT,
}

# Причина-метка: строка, которой в модуле взяться неоткуда.
INJECTED_REASON = "ZzGuardInjectedReason-7f3c"


def _fake_snapshot() -> list:
    """Снимок ровно из выдуманных задач: защищённая и намеренно исключённая."""
    return [(FAKE_PROTECTED_TASK, ARGS_ONE_SPACE),
            (FAKE_EXEMPT_TASK, ARGS_NO_FLAG)]


def _call(pairs, **kwargs):
    """Позвать сверку подставным снимком и ЯВНО заданными параметрами.

    Отличается от `_invoke`: никакого перебора форм вызова — снимок кладётся
    ровно в тот параметр, что разыскан `_seam()`, а всё остальное передаётся
    ИМЕНОВАННО. Иначе «параметр не принят» пряталось бы за запасной формой
    вызова, и слепой пин выглядел бы как зелёный.
    """
    _, fn, first, _, enc = _seam()
    snapshot = [enc(name, args) for name, args in pairs]
    if first.kind is inspect.Parameter.KEYWORD_ONLY:
        return fn(**dict(kwargs, **{first.name: snapshot}))
    return fn(snapshot, **kwargs)


def _statuses_or_fail(result, what: str) -> dict:
    got = _statuses(result)
    if not got:
        pytest.fail("результат сверки (%s) не разбирается на состояния "
                    "контракта §3 %s; получено: %r"
                    % (what, sorted(STATUSES), result))
    return got


def _carries_text(value, needle: str) -> bool:
    """Несёт ли результат сверки данную строку (в любом поле, любой глубины)."""
    if any(needle in text for text in _texts(value, depth=4)):
        return True
    try:
        return needle in repr(value)
    except Exception:  # noqa: BLE001 — сломанный __repr__ не повод падать тут
        return False


def _named_optional_params() -> list:
    """Именованные параметры сверки с умолчанием None (кроме самого снимка)."""
    _, fn, first, _, _ = _seam()
    out = []
    for param in inspect.signature(fn).parameters.values():
        if param.name == first.name:
            continue
        if param.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY):
            continue
        if param.default is not None:
            continue
        out.append(param)
    return out


def _signature_note() -> str:
    fn_name, fn, _, _, _ = _seam()
    try:
        return "%s%s" % (fn_name, inspect.signature(fn))
    except (TypeError, ValueError):  # pragma: no cover — сигнатуры нет
        return fn_name


# ─────────────────────────────────────────────────────────────────────────────
# Розыск параметра, которым внедряется ОЖИДАНИЕ.
# ─────────────────────────────────────────────────────────────────────────────

_EXPECTATION_PARAM = []


def _expectation_param() -> str:
    if _EXPECTATION_PARAM:
        return _EXPECTATION_PARAM[0]

    assert not (set(FAKE_TABLE) & set(EXPECTED_TABLE)), (
        "выдуманные имена %s попали в настоящую таблицу ожидания — розыск "
        "перестал что-либо доказывать, имена-подделки надо сменить"
        % sorted(set(FAKE_TABLE) & set(EXPECTED_TABLE)))

    fn_name = _seam()[0]
    candidates = _named_optional_params()
    if not candidates:
        pytest.fail(
            "у сверки %s нет ни одного ИМЕНОВАННОГО параметра с умолчанием "
            "None, которым можно внедрить ожидание (контракт, шов внедрения). "
            "Сигнатура сейчас: %s. Без этого шва сверку нельзя прогнать на "
            "выдуманном наборе задач: любой её тест зависит от сегодняшнего "
            "состава настоящих." % (fn_name, _signature_note()))

    hits, tried = [], []
    for param in candidates:
        try:
            result = _call(_fake_snapshot(), **{param.name: dict(FAKE_TABLE)})
        except TypeError as exc:
            tried.append("%s: вызов не принят (%r)" % (param.name, exc))
            continue
        except Exception as exc:  # noqa: BLE001 — отказ показываем как есть
            tried.append("%s: сверка выпустила наружу %r" % (param.name, exc))
            continue
        got = _statuses(result)
        if not got:
            tried.append("%s: результат %r не разбирается на состояния"
                         % (param.name, type(result)))
            continue
        if (set(got) == set(FAKE_TABLE)
                and got.get(FAKE_PROTECTED_TASK) == STATUS_OK):
            hits.append(param.name)
        else:
            tried.append("%s: сверка посчитана НЕ по внедрённому ожиданию "
                         "(вышло %r)" % (param.name, got))

    if len(hits) == 1:
        _EXPECTATION_PARAM.append(hits[0])
        return hits[0]
    if not hits:
        pytest.fail(
            "внедрённое ожидание НЕ ИСПОЛЬЗУЕТСЯ: ни один именованный "
            "параметр сверки %s с умолчанием None не изменил результат, хотя "
            "подан выдуманный набор задач %s. Либо параметр молча "
            "игнорируется (и тогда сверку нельзя проверить ни на чём, кроме "
            "сегодняшнего состава настоящих задач), либо шва внедрения нет "
            "вовсе и контракт обязан дозадать ИМЯ параметра.\n"
            "  сигнатура: %s\n  попытки:\n    %s"
            % (fn_name, sorted(FAKE_TABLE), _signature_note(),
               "\n    ".join(tried) or "кандидатов не нашлось"))
    pytest.fail(
        "внедрению ожидания отзываются СРАЗУ НЕСКОЛЬКО параметров (%s): какой "
        "из них ожидание, контракт не говорит — имя надо дозадать. "
        "Сигнатура: %s" % (", ".join(hits), _signature_note()))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 10 — внедрённое ожидание ДЕЙСТВИТЕЛЬНО считается вместо модульного.
#
# Красный ровно на той мутации, что осталась слепой: «функция всегда берёт
# модульную таблицу». При ней в результате окажутся настоящие имена (как
# missing), а выдуманная задача — как unexpected: то есть НЕ то, что внедрено.
# ─────────────────────────────────────────────────────────────────────────────

def test_injected_expectation_is_used_instead_of_the_module_table():
    param = _expectation_param()
    got = _statuses_or_fail(
        _call(_fake_snapshot(), **{param: dict(FAKE_TABLE)}),
        "внедрено ожидание %s=%r" % (param, FAKE_TABLE))

    leaked = sorted(set(got) & set(EXPECTED_TABLE))
    assert leaked == [], (
        "внедрено выдуманное ожидание %s, а в результате сверки всплыли "
        "НАСТОЯЩИЕ задачи %s: параметр проигнорирован и сверка идёт по "
        "модульной таблице. Тогда сверку нельзя прогнать на выдуманном "
        "наборе — любой её тест держится на сегодняшнем составе настоящих "
        "задач и рассыплется, как только состав поменяют."
        % (sorted(FAKE_TABLE), leaked))
    assert set(got) == set(FAKE_TABLE), (
        "внедрено ожидание из %s, а сверка отчиталась по %s"
        % (sorted(FAKE_TABLE), sorted(got)))
    assert got[FAKE_PROTECTED_TASK] == STATUS_OK, (
        "выдуманная задача %s внедрена как %r и несёт `-X utf8`, а сверка по "
        "внедрённому ожиданию сказала %r вместо %r"
        % (FAKE_PROTECTED_TASK, PROTECTION_X_UTF8,
           got[FAKE_PROTECTED_TASK], STATUS_OK))
    assert got[FAKE_EXEMPT_TASK] == STATUS_EXEMPT, (
        "выдуманная задача %s внедрена как %r и в снимке идёт БЕЗ `-X utf8`, "
        "а сверка сказала %r вместо %r: внедрённое исключение не сработало "
        "исключением" % (FAKE_EXEMPT_TASK, PROTECTION_EXEMPT,
                         got[FAKE_EXEMPT_TASK], STATUS_EXEMPT))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 11 — БЕЗ внедрения сверка берёт модульную таблицу (умолчание None).
#
# Обратная сторона пина 10: шов не имеет права стать обязанностью — прежние
# девять пинов зовут сверку одним снимком и обязаны продолжать работать на
# настоящей таблице.
# ─────────────────────────────────────────────────────────────────────────────

def test_expectation_defaults_to_the_module_table_when_not_injected():
    try:
        result = _call([(FAKE_PROTECTED_TASK, ARGS_ONE_SPACE)])
    except TypeError as exc:
        pytest.fail(
            "сверку нельзя позвать БЕЗ ожидания (%r): контракт требует "
            "умолчания None -> модульная таблица, иначе шов внедрения из "
            "удобства превращается в обязанность каждого зовущего носить "
            "ожидание с собой. Сигнатура: %s" % (exc, _signature_note()))
    got = _statuses_or_fail(result, "ожидание не внедрялось")

    assert set(got) == set(EXPECTED_TABLE) | {FAKE_PROTECTED_TASK}, (
        "без внедрения сверка обязана считать по МОДУЛЬНОЙ таблице %s (плюс "
        "лишняя задача из снимка), а отчиталась по %s"
        % (sorted(EXPECTED_TABLE), sorted(got)))
    assert got[FAKE_PROTECTED_TASK] == STATUS_UNEXPECTED, (
        "%s нет в модульной таблице, значит без внедрения это %r, а сверка "
        "сказала %r" % (FAKE_PROTECTED_TASK, STATUS_UNEXPECTED,
                        got[FAKE_PROTECTED_TASK]))
    missing = sorted(n for n, s in got.items() if s == STATUS_MISSING)
    assert missing == sorted(n for n, p in EXPECTED_TABLE.items()
                             if p == PROTECTION_X_UTF8), (
        "снимок пуст на настоящие задачи, поэтому без внедрения все "
        "задачи-%r модульной таблицы обязаны выйти %r; вышли: %s"
        % (PROTECTION_X_UTF8, STATUS_MISSING, missing))


# ─────────────────────────────────────────────────────────────────────────────
# Розыск параметра, которым внедряется СЛОВАРЬ ПРИЧИН исключения, и розыск
# самого модульного словаря причин.
# ─────────────────────────────────────────────────────────────────────────────

_REASON_PARAM = []


def _reason_param() -> str:
    if _REASON_PARAM:
        return _REASON_PARAM[0]

    fn_name = _seam()[0]
    expectation_param = _expectation_param()
    candidates = [p for p in _named_optional_params()
                  if p.name != expectation_param]
    if not candidates:
        pytest.fail(
            "у сверки %s нет ВТОРОГО именованного параметра с умолчанием "
            "None для словаря ПРИЧИН исключения (контракт: «то же самое — для "
            "словаря причин»). Сигнатура: %s"
            % (fn_name, _signature_note()))

    hits, tried = [], []
    for param in candidates:
        kwargs = {expectation_param: dict(FAKE_TABLE),
                  param.name: {FAKE_EXEMPT_TASK: INJECTED_REASON}}
        try:
            result = _call(_fake_snapshot(), **kwargs)
        except TypeError as exc:
            tried.append("%s: вызов не принят (%r)" % (param.name, exc))
            continue
        except Exception as exc:  # noqa: BLE001
            tried.append("%s: сверка выпустила наружу %r" % (param.name, exc))
            continue
        if _carries_text(result, INJECTED_REASON):
            hits.append(param.name)
        else:
            tried.append("%s: внедрённой причины в результате нет (%r)"
                         % (param.name, result))

    if len(hits) == 1:
        _REASON_PARAM.append(hits[0])
        return hits[0]
    if not hits:
        pytest.fail(
            "внедрённая причина исключения НЕ ДОШЛА до результата сверки. "
            "Возможностей две, и обе требуют работы:\n"
            "  1) параметр причин молча игнорируется — тогда исключение "
            "нельзя объяснить на выдуманном наборе задач;\n"
            "  2) причина вообще не выходит из сверки наружу — тогда контракт "
            "обязан дозадать, ГДЕ причина видна в результате (поле записи? "
            "отдельная выдача?), потому что молчаливое исключение "
            "неотличимо от забытой задачи.\n"
            "  сигнатура: %s\n  попытки:\n    %s"
            % (_signature_note(), "\n    ".join(tried)))
    pytest.fail(
        "внедрению причин отзываются сразу несколько параметров (%s) — какой "
        "из них причины, контракт не говорит. Сигнатура: %s"
        % (", ".join(hits), _signature_note()))


def _exempt_names() -> list:
    return sorted(n for n, p in EXPECTED_TABLE.items()
                  if p == PROTECTION_EXEMPT)


def _module_reason_table():
    """(имя атрибута, словарь, {ключ: тексты причины}) или громкий отказ.

    Имя контрактом не задано, поэтому словарь разыскивается по форме: ключи —
    имена задач литеральной таблицы, значения — непустой текст, который не
    является ни защитой, ни состоянием.
    """
    table_attr = _expectation_table()[0]
    out = []
    for name, obj in sorted(vars(mod).items()):
        if name.startswith("__") or name == table_attr:
            continue
        if not isinstance(obj, Mapping) or not obj:
            continue
        if not all(isinstance(k, str) for k in obj):
            continue
        if not set(obj) <= set(EXPECTED_TABLE):
            continue
        texts = {k: [t for t in _texts(v)
                     if t and t not in PROTECTIONS and t not in STATUSES]
                 for k, v in obj.items()}
        if any(not v for v in texts.values()):
            continue
        out.append((name, obj, texts))
    if not out:
        pytest.fail(
            "в app.services.task_encoding нет модульного словаря ПРИЧИН "
            "исключения (ключи — имена исключённых задач %s, значения — текст "
            "причины). Контракт обязан дозадать его ИМЯ и форму: исключение "
            "без записанной причины через полгода неотличимо от забытой "
            "задачи. Публичные имена модуля сейчас: %s"
            % (_exempt_names(),
               ", ".join(sorted(n for n in vars(mod)
                                if not n.startswith("_")))))
    if len(out) > 1:
        pytest.fail(
            "под словарь причин исключения подходят несколько словарей модуля "
            "(%s) — имя надо дозадать" % ", ".join(n for n, _, _ in out))
    return out[0]


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 12 — внедрённая причина исключения доходит до результата.
# ─────────────────────────────────────────────────────────────────────────────

def test_injected_exempt_reason_reaches_the_result():
    expectation_param = _expectation_param()
    reason_param = _reason_param()
    result = _call(_fake_snapshot(),
                   **{expectation_param: dict(FAKE_TABLE),
                      reason_param: {FAKE_EXEMPT_TASK: INJECTED_REASON}})
    assert _carries_text(result, INJECTED_REASON), (
        "внедрена причина %r для выдуманной исключённой задачи %s, а в "
        "результате сверки её нет: словарь причин игнорируется. Тогда "
        "объяснение исключения нельзя проверить на выдуманном наборе — "
        "только на сегодняшних настоящих задачах.\n  результат: %r"
        % (INJECTED_REASON, FAKE_EXEMPT_TASK, result))
    got = _statuses_or_fail(result, "внедрены и ожидание, и причины")
    assert got.get(FAKE_EXEMPT_TASK) == STATUS_EXEMPT, (
        "причина внедрена, а состояние выдуманной исключённой задачи вышло "
        "%r вместо %r: причина не имеет права менять вердикт"
        % (got.get(FAKE_EXEMPT_TASK), STATUS_EXEMPT))


# ─────────────────────────────────────────────────────────────────────────────
# ПИН 13 — БЕЗ внедрения причина берётся из модульного словаря.
# ─────────────────────────────────────────────────────────────────────────────

def test_exempt_reason_defaults_to_the_module_table_when_not_injected():
    _reason_param()  # шов обязан существовать и быть наблюдаемым
    attr, _, texts = _module_reason_table()

    uncovered = sorted(set(_exempt_names()) - set(texts))
    assert uncovered == [], (
        "в модульном словаре причин %s нет объяснения для исключённых задач "
        "%s: исключение без записанной причины — молчаливое исключение, через "
        "полгода его не отличить от забытой задачи" % (attr, uncovered))

    exempt = _exempt_names()[0]
    result = _call(_all_protected_snapshot() + [(exempt, ARGS_NO_FLAG)])
    if not any(_carries_text(result, text) for text in texts[exempt]):
        pytest.fail(
            "без внедрения причина исключения %s обязана прийти из модульного "
            "словаря %s (%r), а в результате сверки её нет: умолчание None -> "
            "модульное значение не работает.\n  результат: %r"
            % (exempt, attr, texts[exempt], result))
    assert not _carries_text(result, INJECTED_REASON), (
        "причина не внедрялась, а в результате всплыла подставная метка %r: "
        "внедрение протекло между вызовами" % INJECTED_REASON)
