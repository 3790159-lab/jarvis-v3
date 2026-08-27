# -*- coding: utf-8 -*-
"""Сторожа DEV-59, часть 3: СВЕРКА — семь состояний, причины, таблица, чистота.

Стережётся `compare_tasks(..., read_script=...)`, литеральная таблица §7,
литеральный список причин §6.1, строгий порядок проверок §6.2, осознанная
асимметрия §6.3, дубль в снимке §6.4, мусорная защита §6.5, `exempt` без
причины §6.6, совместимость §6.7 и чистота модуля §2.

Написано ОТ СПЕКИ И КОНТРАКТА, БЕЗ просмотра реализации.

ЛОВУШКА №5, ради которой файл и существует: `unreadable` != зелёное. Сторож,
зелёный при отсутствии ресурса, — не сторож; это уже стоило одного
молчаливого сигнала.

ГРАНИЦА, названная вслух: ключ записи снимка для исполнителя контракт зовёт
`Execute` и другого написания не даёт, поэтому здесь он пинится буквально.
Если реализация назвала его иначе — это расхождение контракта, а не дефект
кода: звать сведение.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from app.services import task_encoding as mod


UTF8_BOM = b"\xef\xbb\xbf"


def _need(name):
    obj = getattr(mod, name, None)
    if obj is None:
        pytest.fail(
            "контракт DEV-59 §2/§7: в app/services/task_encoding.py нет `%s`"
            % name)
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# ЛИТЕРАЛЬНЫЕ ожидания. Не выводить из кода: выведенный список согласен с
# кодом по определению и промолчит ровно там, где код забыл.
# ─────────────────────────────────────────────────────────────────────────────

# Контракт §7 — решение владельца 24.08. Ровно 14 имён, ровно эти значения.
# 🔢 14, А НЕ 13, С 28.08: арка volska-panel завела ВТОРУЮ панельную задачу
# (§5.5). Число правится ТЕМ ЖЕ коммитом, что и сама задача, намеренно:
# контракт парка задач — решение владельца, и он обязан узнать о
# четырнадцатой ЗДЕСЬ, на суите, а не найти её в ночном отчёте как
# `unexpected`.
EXPECTED_TABLE_14 = {
    "JarvisStateBackup": "x_utf8",
    "JarvisRestoreDrill": "x_utf8",
    "JarvisChatterCacheDigest": "x_utf8",
    "JarvisDrillNightly": "x_utf8",
    "JarvisIgTokenRefresh": "exempt",
    "JarvisBackendGuardian": "ps_console",
    "JarvisBotGuardian": "ps_console",
    "JarvisChatterGuardian": "ps_console",
    "JarvisHealthchecksPing": "ps_console",
    "JarvisInfraRestartCloudflared": "ps_console",
    "JarvisOpsWatchdog": "ps_console",
    "JarvisPanelClientGuardian": "ps_console",
    "JarvisPanelClientGuardianVolska": "ps_console",
    "JarvisSniperDetached": "ps_console",
}

# Контракт §6.1 — причина -> состояние, дословно по таблице. Других быть не
# должно.
REASON_TO_STATE = {
    "ok": "ok",
    "exempt": "exempt",
    "missing": "missing",
    "unexpected": "unexpected",
    "unprotected": "unprotected",
    "declared_late": "unprotected",
    "kind_mismatch": "kind_mismatch",
    "no_reader": "unreadable",
    "reader_failed": "unreadable",
    "arguments_unparsed": "unreadable",
    "no_arguments": "unreadable",
    "execute_unknown": "unreadable",
    "decode_failed": "unreadable",
    "duplicate_in_snapshot": "unreadable",
    "unknown_protection": "unreadable",
    "exempt_without_reason": "unreadable",
}

STATES_7 = {
    "ok", "unprotected", "exempt", "missing", "unexpected",
    "unreadable", "kind_mismatch",
}


# ── Модельные .ps1 ──────────────────────────────────────────────────────────

PS_OK = ("param()\n"
         "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
         "Write-Host 'поехали'\n")

PS_LATE = ("param()\n"
           "Write-Host 'поехали'\n"
           "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n")

PS_NONE = ("param()\n"
           "Write-Host 'поехали'\n")

PS_PATH = r"C:\jarvis\scripts\ops_watchdog_detached.ps1"
PS_ARGS = (r'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
           r'-File "%s"' % PS_PATH)

PS_EXPECT = {"JarvisOpsWatchdog": "ps_console"}
PY_EXPECT = {"JarvisStateBackup": "x_utf8"}

PY_ARGS_OK = r'-X utf8 "C:\jarvis\scripts\state_backup.py"'
PY_ARGS_BAD = r'"C:\jarvis\scripts\state_backup.py"'


class Reader:
    """Подставной читатель: по пути отдаёт СЫРЫЕ БАЙТЫ.

    Файлов на диске не нужно — ровно затем шов и выбран (спека §3, вариант 4).
    """

    def __init__(self, files=None, boom=None):
        self.files = dict(files or {})
        self.boom = boom
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if self.boom is not None:
            raise self.boom
        if path not in self.files:
            raise KeyError(path)
        return self.files[path]


def _reader_ok(source=PS_OK, path=PS_PATH, bom=True):
    raw = source.encode("utf-8")
    return Reader({path: (UTF8_BOM + raw) if bom else source.encode("cp1251")})


def _rec(name, arguments=None, execute=None):
    """Запись снимка. Ключи отсутствуют, когда сборщик их не принёс."""
    r = {"name": name}
    if arguments is not None:
        r["arguments"] = arguments
    if execute is not None:
        r["Execute"] = execute
    return r


def _verdicts(snapshot, **kw):
    out = _need("compare_tasks")(snapshot, **kw)
    for v in out:
        assert v.state in STATES_7, (
            "состояний ровно семь (§6), получено %r" % (v.state,))
        assert v.reason, (
            "«`reason` есть на КАЖДОМ вердикте, включая зелёный» (§6.1); "
            "у %r причины нет" % (v.task,))
        assert v.reason in REASON_TO_STATE, (
            "причина %r не из литерального списка §6.1" % (v.reason,))
        assert REASON_TO_STATE[v.reason] == v.state, (
            "§6.1: причина %r обязана жить под состоянием %r, а не %r"
            % (v.reason, REASON_TO_STATE[v.reason], v.state))
    names = [v.task for v in out]
    assert len(names) == len(set(names)), (
        "на одно имя обязан быть ОДИН вердикт, получено %r" % (names,))
    return {v.task: v for v in out}


def _one(snapshot, name, **kw):
    got = _verdicts(snapshot, **kw)
    assert name in got, "вердикта про %r нет вовсе: %r" % (name, sorted(got))
    return got[name]


# ─────────────────────────────────────────────────────────────────────────────
# §7 — ЛИТЕРАЛЬНАЯ ТАБЛИЦА, в ОБЕ стороны
# ─────────────────────────────────────────────────────────────────────────────

def test_table_has_exactly_the_fourteen_names_and_values():
    table = _need("TASK_ENCODING_EXPECTATION")
    assert dict(table) == EXPECTED_TABLE_14


def test_table_has_no_fifteenth_name():
    """Вторая сторона: имя, заведённое и забытое, обязано быть видно как
    расхождение с ЛИТЕРАЛЬНЫМ списком, а не раствориться в нём."""
    table = _need("TASK_ENCODING_EXPECTATION")
    assert len(table) == 14
    extra = set(table) - set(EXPECTED_TABLE_14)
    missing = set(EXPECTED_TABLE_14) - set(table)
    assert not extra, "в таблице лишние имена: %r" % (sorted(extra),)
    assert not missing, "из таблицы пропали имена: %r" % (sorted(missing),)


@pytest.mark.parametrize("name,protection", sorted(EXPECTED_TABLE_14.items()))
def test_each_of_the_fourteen_names_carries_its_own_value(name, protection):
    table = _need("TASK_ENCODING_EXPECTATION")
    assert table.get(name) == protection


def test_the_three_protection_constants():
    assert _need("PROTECTION_X_UTF8") == "x_utf8"
    assert _need("PROTECTION_PS_CONSOLE") == "ps_console"
    assert _need("PROTECTION_EXEMPT") == "exempt"


def test_the_seven_state_constants():
    assert _need("STATE_OK") == "ok"
    assert _need("STATE_UNPROTECTED") == "unprotected"
    assert _need("STATE_EXEMPT") == "exempt"
    assert _need("STATE_MISSING") == "missing"
    assert _need("STATE_UNEXPECTED") == "unexpected"
    assert _need("STATE_UNREADABLE") == "unreadable"
    assert _need("STATE_KIND_MISMATCH") == "kind_mismatch"


def test_table_values_are_only_the_three_known_protections():
    table = _need("TASK_ENCODING_EXPECTATION")
    assert set(table.values()) <= {"x_utf8", "ps_console", "exempt"}


def test_every_exempt_in_the_table_has_a_recorded_reason():
    """§6.6: «вынесено осознанно» без записанной причины НЕДОКАЗУЕМО."""
    table = _need("TASK_ENCODING_EXPECTATION")
    reasons = _need("EXEMPT_REASONS")
    for name, protection in table.items():
        if protection == "exempt":
            assert reasons.get(name), (
                "у исключённой задачи %r причина не записана" % (name,))


# ─────────────────────────────────────────────────────────────────────────────
# Три вида защиты В ОБЕ СТОРОНЫ
# ─────────────────────────────────────────────────────────────────────────────

def test_x_utf8_both_ways():
    ok = _one([_rec("JarvisStateBackup", PY_ARGS_OK)], "JarvisStateBackup",
              expectation=PY_EXPECT)
    assert (ok.state, ok.reason) == ("ok", "ok")
    bad = _one([_rec("JarvisStateBackup", PY_ARGS_BAD)], "JarvisStateBackup",
               expectation=PY_EXPECT)
    assert (bad.state, bad.reason) == ("unprotected", "unprotected")


def test_ps_console_both_ways():
    ok = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
              "JarvisOpsWatchdog",
              expectation=PS_EXPECT, read_script=_reader_ok(PS_OK))
    assert (ok.state, ok.reason) == ("ok", "ok")
    bad = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
               "JarvisOpsWatchdog",
               expectation=PS_EXPECT, read_script=_reader_ok(PS_NONE))
    assert (bad.state, bad.reason) == ("unprotected", "unprotected")


def test_exempt_both_ways():
    """С записанной причиной — `exempt`; без неё — `unreadable` (§6.6)."""
    with_reason = _one([_rec("X", "")], "X",
                       expectation={"X": "exempt"},
                       exempt_reasons={"X": "решение владельца 24.08"})
    assert (with_reason.state, with_reason.reason) == ("exempt", "exempt")

    without = _one([_rec("X", "")], "X",
                   expectation={"X": "exempt"}, exempt_reasons={})
    assert (without.state, without.reason) == \
        ("unreadable", "exempt_without_reason")


def test_missing_and_unexpected():
    got = _verdicts([_rec("JarvisSomethingNew", "")], expectation=PY_EXPECT)
    assert (got["JarvisStateBackup"].state,
            got["JarvisStateBackup"].reason) == ("missing", "missing")
    assert (got["JarvisSomethingNew"].state,
            got["JarvisSomethingNew"].reason) == ("unexpected", "unexpected")


# ─────────────────────────────────────────────────────────────────────────────
# §6.1 — `unreadable` в КАЖДОЙ своей причине, по пину на причину
# ─────────────────────────────────────────────────────────────────────────────

def test_unreadable_no_reader():
    """«`read_script=None`, а в ожидании есть `ps_console`» — fail-closed."""
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT, read_script=None)
    assert (v.state, v.reason) == ("unreadable", "no_reader")


def test_unreadable_no_reader_when_argument_omitted_entirely():
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT)
    assert (v.state, v.reason) == ("unreadable", "no_reader")


def test_unreadable_reader_failed():
    """«Любое исключение читателя означает "не прочитали", и оно НЕ вылетает
    наружу» (§2)."""
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=Reader(boom=OSError("файла нет")))
    assert (v.state, v.reason) == ("unreadable", "reader_failed")


@pytest.mark.parametrize("boom", [
    OSError("файла нет"),
    PermissionError("нет доступа"),
    RuntimeError("что угодно"),
    ValueError("и такое бывает"),
    UnicodeDecodeError("cp1251", b"\x98", 0, 1, "не определён"),
])
def test_reader_exception_never_escapes(boom):
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=Reader(boom=boom))
    assert (v.state, v.reason) == ("unreadable", "reader_failed")


@pytest.mark.parametrize("args", [
    '-NoProfile -Command "& {Write-Host 1}"',
    "-NoProfile -EncodedCommand VwByAGkAdABlAA==",
    "-NoProfile -ExecutionPolicy Bypass",
])
def test_unreadable_arguments_unparsed(args):
    v = _one([_rec("JarvisOpsWatchdog", args, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("unreadable", "arguments_unparsed")


def test_unreadable_no_arguments_when_key_is_absent():
    v = _one([_rec("JarvisOpsWatchdog", None, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("unreadable", "no_arguments")


def test_unreadable_no_arguments_when_string_is_empty():
    v = _one([_rec("JarvisOpsWatchdog", "", "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("unreadable", "no_arguments")


def test_unreadable_execute_unknown_when_absent():
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, None)],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("unreadable", "execute_unknown")


@pytest.mark.parametrize("execute", ["cmd.exe", "wscript.exe", "", "   "])
def test_unreadable_execute_unknown_when_unrecognised(execute):
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, execute)],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("unreadable", "execute_unknown")


def test_unreadable_decode_failed():
    """Байт `0x98` в cp1251 не определён; файл без BOM не декодируется."""
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=Reader({PS_PATH: b"\x98\x98\x98"}))
    assert (v.state, v.reason) == ("unreadable", "decode_failed")


def test_unreadable_duplicate_in_snapshot():
    snapshot = [_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe"),
                _rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")]
    v = _one(snapshot, "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("unreadable", "duplicate_in_snapshot")


@pytest.mark.parametrize("protection", ["ps_console_", "utf8", "true", "", "1"])
def test_unreadable_unknown_protection(protection):
    """«Не падать исключением и не считать зелёным» (§6.5)."""
    v = _one([_rec("X", PS_ARGS, "powershell.exe")], "X",
             expectation={"X": protection}, read_script=_reader_ok())
    assert (v.state, v.reason) == ("unreadable", "unknown_protection")


def test_unreadable_exempt_without_reason():
    v = _one([_rec("X", "")], "X", expectation={"X": "exempt"},
             exempt_reasons={})
    assert (v.state, v.reason) == ("unreadable", "exempt_without_reason")


# ─────────────────────────────────────────────────────────────────────────────
# §6.2 — СТРОГИЙ порядок проверок. Первое сработавшее выигрывает.
# ─────────────────────────────────────────────────────────────────────────────

def test_order_duplicate_beats_kind_mismatch():
    """Ступень 1 выше ступени 4."""
    snapshot = [_rec("JarvisOpsWatchdog", PS_ARGS, "python.exe"),
                _rec("JarvisOpsWatchdog", PS_ARGS, "python.exe")]
    v = _one(snapshot, "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert v.reason == "duplicate_in_snapshot"


def test_order_missing_beats_execute_unknown():
    """Ступень 2 выше ступени 3: задачи нет вовсе, исполнителя тоже нет."""
    v = _one([], "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("missing", "missing")


def test_order_execute_unknown_beats_no_arguments():
    """Ступень 3 выше ступени 5."""
    v = _one([_rec("JarvisOpsWatchdog", None, None)], "JarvisOpsWatchdog",
             expectation=PS_EXPECT, read_script=_reader_ok())
    assert v.reason == "execute_unknown"


def test_order_kind_mismatch_beats_no_arguments_and_no_reader():
    """Ступень 4 выше ступеней 5 и 6."""
    v = _one([_rec("JarvisOpsWatchdog", None, "python.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT, read_script=None)
    assert (v.state, v.reason) == ("kind_mismatch", "kind_mismatch")


def test_order_no_arguments_beats_no_reader():
    """Ступень 5 выше ступени 6."""
    v = _one([_rec("JarvisOpsWatchdog", None, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT, read_script=None)
    assert v.reason == "no_arguments"


def test_order_no_reader_beats_arguments_unparsed():
    """Ступень 6 выше ступени 7: читателя нет, и разбирать нечего."""
    v = _one([_rec("JarvisOpsWatchdog", "-Command foo", "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT, read_script=None)
    assert v.reason == "no_reader"


def test_order_arguments_unparsed_beats_reader_failed():
    """Ступень 7 выше ступени 8: читатель бы бросил, но его не зовут."""
    reader = Reader(boom=OSError("бросил бы"))
    v = _one([_rec("JarvisOpsWatchdog", "-Command foo", "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT, read_script=reader)
    assert v.reason == "arguments_unparsed"
    assert reader.calls == [], (
        "путь не разобран — читателя звать нечем и незачем")


def test_order_reader_failed_beats_decode_failed():
    """Ступень 8 выше ступени 9."""
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=Reader(boom=OSError("нет")))
    assert v.reason == "reader_failed"


def test_order_unprotected_then_declared_late_then_ok():
    """Ступени 10, 11, 12 — три исхода на трёх версиях одного файла."""
    none_ = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
                 "JarvisOpsWatchdog", expectation=PS_EXPECT,
                 read_script=_reader_ok(PS_NONE))
    assert (none_.state, none_.reason) == ("unprotected", "unprotected")

    late = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
                "JarvisOpsWatchdog", expectation=PS_EXPECT,
                read_script=_reader_ok(PS_LATE))
    assert (late.state, late.reason) == ("unprotected", "declared_late")

    ok = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
              "JarvisOpsWatchdog", expectation=PS_EXPECT,
              read_script=_reader_ok(PS_OK))
    assert (ok.state, ok.reason) == ("ok", "ok")


def test_declared_late_lives_under_unprotected_not_a_state_of_its_own():
    """«`declared_late` живёт под `unprotected` осознанно: действие одно и то
    же — править ФАЙЛ. Но причина разная, поэтому её видно машинно» (§6.1)."""
    late = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
                "JarvisOpsWatchdog", expectation=PS_EXPECT,
                read_script=_reader_ok(PS_LATE))
    none_ = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe")],
                 "JarvisOpsWatchdog", expectation=PS_EXPECT,
                 read_script=_reader_ok(PS_NONE))
    assert late.state == none_.state == "unprotected"
    assert late.reason != none_.reason, (
        "дедуп по причине не увидит смены одного красного на другое")


# ─────────────────────────────────────────────────────────────────────────────
# §6.3 — kind_mismatch и ОСОЗНАННАЯ АСИММЕТРИЯ
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("execute", [
    "powershell.exe",
    "pwsh.exe",
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "POWERSHELL.EXE",
])
def test_kind_mismatch_x_utf8_expected_but_powershell_runs(execute):
    v = _one([_rec("JarvisStateBackup", PY_ARGS_OK, execute)],
             "JarvisStateBackup", expectation=PY_EXPECT)
    assert (v.state, v.reason) == ("kind_mismatch", "kind_mismatch"), (
        "задачу перенаправили на другой исполнитель — признак стал не про неё")


@pytest.mark.parametrize("execute", [
    "python.exe",
    "pythonw.exe",
    r"C:\jarvis\.venv\Scripts\python.exe",
    "PYTHON.EXE",
])
def test_kind_mismatch_ps_console_expected_but_python_runs(execute):
    v = _one([_rec("JarvisOpsWatchdog", PS_ARGS, execute)],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok())
    assert (v.state, v.reason) == ("kind_mismatch", "kind_mismatch")


def test_the_asymmetry_itself_both_halves_in_one_pin():
    """§6.3. У `x_utf8` отсутствие `Execute` ПРОЩАЕТСЯ, у `ps_console` — НЕТ.

    Оба случая в одном пине намеренно: асимметрия обязана быть видна как
    ОДНО решение. Иначе через месяц её примут за дырку и «починят», а вместе
    с ней сломают 17 существующих пинов — их записи снимка `Execute` не несут.
    """
    py = _one([_rec("JarvisStateBackup", PY_ARGS_OK, None)],
              "JarvisStateBackup", expectation=PY_EXPECT)
    assert (py.state, py.reason) == ("ok", "ok"), (
        "у `x_utf8` отсутствие `Execute` обязано ПРОЩАТЬСЯ: вид не сверяется")

    ps = _one([_rec("JarvisOpsWatchdog", PS_ARGS, None)],
              "JarvisOpsWatchdog", expectation=PS_EXPECT,
              read_script=_reader_ok())
    assert (ps.state, ps.reason) == ("unreadable", "execute_unknown"), (
        "у `ps_console` отсутствие `Execute` обязано быть fail-closed")


@pytest.mark.parametrize("execute", [None, "cmd.exe", "", "чтоугодно.exe"])
def test_x_utf8_with_absent_or_unknown_execute_behaves_as_before(execute):
    """«вид НЕ сверяется, поведение как было» (§6.3) — обе половинки."""
    ok = _one([_rec("JarvisStateBackup", PY_ARGS_OK, execute)],
              "JarvisStateBackup", expectation=PY_EXPECT)
    assert (ok.state, ok.reason) == ("ok", "ok")
    bad = _one([_rec("JarvisStateBackup", PY_ARGS_BAD, execute)],
               "JarvisStateBackup", expectation=PY_EXPECT)
    assert (bad.state, bad.reason) == ("unprotected", "unprotected")


def test_kind_mismatch_only_on_POSITIVE_recognition_of_a_foreign_kind():
    """«`kind_mismatch` срабатывает только на ПОЛОЖИТЕЛЬНОМ опознании чужого
    вида» — неузнанный исполнитель у python-задачи это НЕ mismatch."""
    v = _one([_rec("JarvisStateBackup", PY_ARGS_OK, "cmd.exe")],
             "JarvisStateBackup", expectation=PY_EXPECT)
    assert v.state != "kind_mismatch"


# ─────────────────────────────────────────────────────────────────────────────
# §6.4 — дубль касается ОБОИХ видов защиты и случая `unexpected`
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_for_x_utf8_task():
    snapshot = [_rec("JarvisStateBackup", PY_ARGS_OK),
                _rec("JarvisStateBackup", PY_ARGS_BAD)]
    v = _one(snapshot, "JarvisStateBackup", expectation=PY_EXPECT)
    assert (v.state, v.reason) == ("unreadable", "duplicate_in_snapshot"), (
        "молча оставлять последнюю запись больше нельзя: неизвестно, о какой "
        "записи вердикт")


def test_duplicate_for_an_unexpected_name():
    snapshot = [_rec("JarvisSomethingNew", ""),
                _rec("JarvisSomethingNew", "")]
    v = _one(snapshot, "JarvisSomethingNew", expectation=PY_EXPECT)
    assert (v.state, v.reason) == ("unreadable", "duplicate_in_snapshot")


# ─────────────────────────────────────────────────────────────────────────────
# §6.7 и совместимость с 17 существующими пинами
# ─────────────────────────────────────────────────────────────────────────────

def test_compare_tasks_works_with_no_keyword_arguments_at_all():
    """«включая вызов `compare_tasks(snapshot)` без единого именованного
    аргумента» (§6.7). Ровно так его зовут 17 существующих пинов."""
    snapshot = [
        _rec("JarvisStateBackup", PY_ARGS_OK),
        _rec("JarvisRestoreDrill", PY_ARGS_BAD),
    ]
    got = _verdicts(snapshot)
    assert (got["JarvisStateBackup"].state,
            got["JarvisStateBackup"].reason) == ("ok", "ok")
    assert (got["JarvisRestoreDrill"].state,
            got["JarvisRestoreDrill"].reason) == ("unprotected", "unprotected")
    assert got["JarvisIgTokenRefresh"].state == "exempt"


def test_pure_python_expectation_is_untouched_by_a_missing_reader():
    """«Ничего не меняется. Чисто python-ожидание работает как раньше» (§6.7)."""
    snapshot = [_rec("JarvisStateBackup", PY_ARGS_OK),
                _rec("JarvisRestoreDrill", PY_ARGS_BAD)]
    got = _verdicts(snapshot, expectation={
        "JarvisStateBackup": "x_utf8", "JarvisRestoreDrill": "x_utf8"},
        read_script=None)
    assert {v.state for v in got.values()} == {"ok", "unprotected"}
    assert "unreadable" not in {v.state for v in got.values()}


def test_x_utf8_task_without_arguments_is_still_unprotected_not_unreadable():
    """Ступень «аргументов нет» — про `ps_console`. У python-задачи поведение
    прежнее, иначе поедут существующие пины."""
    v = _one([_rec("JarvisStateBackup", None)], "JarvisStateBackup",
             expectation=PY_EXPECT)
    assert (v.state, v.reason) == ("unprotected", "unprotected")


def test_reader_is_not_called_for_python_tasks():
    """Признак python-задачи — строка аргументов; лезть за её файлом незачем."""
    reader = Reader(boom=OSError("звать не должны"))
    _verdicts([_rec("JarvisStateBackup", PY_ARGS_OK)],
              expectation=PY_EXPECT, read_script=reader)
    assert reader.calls == []


def test_reader_is_called_with_exactly_the_parsed_path_without_the_tail():
    """Сцепка §5 и §3: читателю уходит путь БЕЗ хвоста `-Slug yarina`."""
    path = r"C:\jarvis\scripts\panel_client_guardian_detached.ps1"
    args = r'-NoProfile -File "%s" -Slug yarina' % path
    reader = Reader({path: UTF8_BOM + PS_OK.encode("utf-8")})
    v = _one([_rec("JarvisPanelClientGuardian", args, "powershell.exe")],
             "JarvisPanelClientGuardian",
             expectation={"JarvisPanelClientGuardian": "ps_console"},
             read_script=reader)
    assert reader.calls == [path]
    assert v.state == "ok"


def test_verdict_order_is_expectation_then_snapshot():
    expectation = {"JarvisStateBackup": "x_utf8",
                   "JarvisOpsWatchdog": "ps_console"}
    snapshot = [_rec("JarvisZzz", ""),
                _rec("JarvisOpsWatchdog", PS_ARGS, "powershell.exe"),
                _rec("JarvisStateBackup", PY_ARGS_OK)]
    out = _need("compare_tasks")(snapshot, expectation=expectation,
                                 read_script=_reader_ok())
    assert [v.task for v in out] == \
        ["JarvisStateBackup", "JarvisOpsWatchdog", "JarvisZzz"]


def test_a_bomless_cp1251_script_is_read_by_the_same_rule():
    """`backend_guardian_detached.ps1` BOM не несёт — путь живой (§4)."""
    v = _one([_rec("JarvisBackendGuardian", PS_ARGS, "powershell.exe")],
             "JarvisBackendGuardian",
             expectation={"JarvisBackendGuardian": "ps_console"},
             read_script=_reader_ok(
                 "# Сторож бэкенда — держит :8010\n" + PS_OK, bom=False))
    assert (v.state, v.reason) == ("ok", "ok")


# ─────────────────────────────────────────────────────────────────────────────
# §2/§3 спеки — ЧИСТОТА модуля. «Он зовёт то, что ему дали.»
# ─────────────────────────────────────────────────────────────────────────────

FORBIDDEN_IMPORTS = {"os", "pathlib", "subprocess"}


def _module_tree():
    return ast.parse(inspect.getsource(mod))


@pytest.mark.parametrize("banned", sorted(FORBIDDEN_IMPORTS))
def test_module_does_not_import_the_disk(banned):
    """«Модуль по-прежнему НЕ импортирует `os`, `pathlib`, `subprocess`».

    Стоит здесь потому, что чистота — не гигиена, а условие проверяемости:
    сверка, ходящая на диск сама, проверялась бы только на машине с этими
    файлами, то есть была бы стендом, а не сторожем.
    """
    found = []
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            found += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module.split(".")[0])
    assert banned not in found, "модуль импортирует `%s`" % banned


def test_module_namespace_is_free_of_the_disk():
    for banned in FORBIDDEN_IMPORTS:
        assert banned not in vars(mod), (
            "в пространстве имён модуля лежит `%s`" % banned)


def test_module_never_opens_a_file():
    calls = []
    for node in ast.walk(_module_tree()):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in {"open", "exec", "eval"}:
            calls.append(fn.id)
        if isinstance(fn, ast.Attribute) and fn.attr in {
                "open", "read_bytes", "read_text", "popen", "system"}:
            calls.append(fn.attr)
    assert calls == [], "модуль сам ходит на диск: %r" % (calls,)


# ═════════════════════════════════════════════════════════════════════════════
# АМЕНДМЕНТ А.4 — дозадано по вопросам, поднятым на первом круге.
# ═════════════════════════════════════════════════════════════════════════════

# ── Написание ключа исполнителя ─────────────────────────────────────────────
#
# На первом круге контракт называл только `Execute` и другого написания не
# давал; я пинил его буквально и назвал это первым кандидатом в ложный
# красный. Дозадано: понимаются РОВНО три написания, прочие — «`Execute`
# отсутствует».

EXECUTE_KEYS_UNDERSTOOD = ["Execute", "execute", "Executable"]
EXECUTE_KEYS_NOT_UNDERSTOOD = ["EXECUTE", "executable", "Exec", "Action",
                               "ExecutePath", "TaskExecute"]


def _rec_key(name, arguments, key, execute):
    """Запись снимка, где исполнитель лежит под ЗАДАННЫМ ключом."""
    r = {"name": name}
    if arguments is not None:
        r["arguments"] = arguments
    r[key] = execute
    return r


@pytest.mark.parametrize("key", EXECUTE_KEYS_UNDERSTOOD)
def test_a4_execute_key_spelling_is_understood(key):
    v = _one([_rec_key("JarvisOpsWatchdog", PS_ARGS, key, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok(PS_OK))
    assert (v.state, v.reason) == ("ok", "ok"), (
        "ключ %r обязан читаться как исполнитель (А.4)" % key)


@pytest.mark.parametrize("key", EXECUTE_KEYS_UNDERSTOOD)
def test_a4_understood_key_also_powers_kind_mismatch(key):
    """Вторая сторона: узнанный ключ обязан РАБОТАТЬ, а не просто не мешать."""
    v = _one([_rec_key("JarvisStateBackup", PY_ARGS_OK, key, "powershell.exe")],
             "JarvisStateBackup", expectation=PY_EXPECT)
    assert (v.state, v.reason) == ("kind_mismatch", "kind_mismatch")


@pytest.mark.parametrize("key", EXECUTE_KEYS_NOT_UNDERSTOOD)
def test_a4_other_key_spellings_mean_execute_is_absent(key):
    """«Другие написания не понимаются: запись без узнаваемого ключа идёт по
    §6.3 как "`Execute` отсутствует"» — то есть fail-closed у `ps_console`."""
    v = _one([_rec_key("JarvisOpsWatchdog", PS_ARGS, key, "powershell.exe")],
             "JarvisOpsWatchdog", expectation=PS_EXPECT,
             read_script=_reader_ok(PS_OK))
    assert (v.state, v.reason) == ("unreadable", "execute_unknown")


@pytest.mark.parametrize("key", EXECUTE_KEYS_NOT_UNDERSTOOD)
def test_a4_other_key_spellings_do_not_trigger_kind_mismatch(key):
    """И зеркало, ради асимметрии §6.3: у `x_utf8` неузнанный ключ = вид не
    сверяется, поведение как было. Иначе 17 существующих пинов поедут."""
    v = _one([_rec_key("JarvisStateBackup", PY_ARGS_OK, key, "powershell.exe")],
             "JarvisStateBackup", expectation=PY_EXPECT)
    assert (v.state, v.reason) == ("ok", "ok")


# ── `python3.exe` узнаётся как python ───────────────────────────────────────

def test_a4_python3_exe_is_recognised_as_python():
    """Сужение отменено амендментом: живой венв может называться иначе, а
    НЕПРИЗНАННЫЙ python у `x_utf8`-задачи молча отключает сверку вида — то
    есть даёт зелёное там, где вид не сверялся вовсе.
    """
    ps = _one([_rec("JarvisOpsWatchdog", PS_ARGS, "python3.exe")],
              "JarvisOpsWatchdog", expectation=PS_EXPECT,
              read_script=_reader_ok(PS_OK))
    assert (ps.state, ps.reason) == ("kind_mismatch", "kind_mismatch")


@pytest.mark.parametrize("execute", [
    "python3.exe",
    "PYTHON3.EXE",
    r"C:\jarvis\.venv\Scripts\python3.exe",
])
def test_a4_python3_is_the_right_kind_for_an_x_utf8_task(execute):
    """Вторая сторона: у python-задачи `python3.exe` — СВОЙ вид, не чужой."""
    ok = _one([_rec("JarvisStateBackup", PY_ARGS_OK, execute)],
              "JarvisStateBackup", expectation=PY_EXPECT)
    assert (ok.state, ok.reason) == ("ok", "ok")
    bad = _one([_rec("JarvisStateBackup", PY_ARGS_BAD, execute)],
               "JarvisStateBackup", expectation=PY_EXPECT)
    assert (bad.state, bad.reason) == ("unprotected", "unprotected")


# ── `exempt` сильнее дубля ──────────────────────────────────────────────────

def test_a4_exempt_beats_duplicate_in_snapshot():
    """«`exempt` сильнее дубля — как и сильнее отсутствия» (А.4).

    Довод тот же, что записан у «исключение сильнее отсутствия»: предмет
    сторожа — кодировка. Красное про исключённую задачу горит по причине, к
    кодировке отношения не имеющей, а «красное при полном порядке» приучает
    не читать красное.
    """
    snapshot = [_rec("X", ""), _rec("X", "")]
    v = _one(snapshot, "X", expectation={"X": "exempt"},
             exempt_reasons={"X": "решение владельца 24.08"})
    assert (v.state, v.reason) == ("exempt", "exempt")


def test_a4_exempt_without_reason_still_wins_over_duplicate():
    """Следствие, которое я вывожу сам: раз ветка `exempt` решает раньше
    дубля, то и §6.6 решает внутри неё раньше дубля.

    Названо вслух как МОЁ прочтение: амендмент говорит «`exempt` сильнее
    дубля», но про `exempt` БЕЗ причины вместе с дублём не говорит.
    """
    snapshot = [_rec("X", ""), _rec("X", "")]
    v = _one(snapshot, "X", expectation={"X": "exempt"}, exempt_reasons={})
    assert (v.state, v.reason) == ("unreadable", "exempt_without_reason")
