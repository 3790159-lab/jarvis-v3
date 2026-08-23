# -*- coding: utf-8 -*-
"""Сторожа DEV-52: ремень обязан назвать ПОЖИРАТЕЛЯ, а не текущий тест.

Написаны ОТ СПЕКИ `docs/superpowers/specs/2026-08-23-dev52-belt-names-the-eater.md`
(§3 контракт данных, §4 границы, §5 что снимается, §6 форма, §7 пины),
ДО и БЕЗ просмотра реализации: иначе тест и код унаследуют одно неверное
допущение.

Все ожидаемые строки — ЛИТЕРАЛЬНЫЕ. Выведенный из кода список согласен с кодом
по определению и молчит там, где код забыл.

Чистота: настоящий psutil здесь не перебирает процессы. Источник процессов
подставной; классы исключений psutil берутся импортом (без обращения к живым
процессам) только затем, чтобы фальшивый процесс отказывал ТЕМ ЖЕ типом
исключения, который реализация обязана ловить по §5.
"""
from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.services.suite_ram_belt import (
    REASON_FREE,
    REASON_RSS,
    Limits,
    render_report,
)
from app.services import suite_ram_belt as belt_mod


# ─────────────────────────────────────────────────────────────────────────────
# Литеральные ожидания. Не выводить из кода.
# ─────────────────────────────────────────────────────────────────────────────

# §6: закрывающая строка ветки REASON_RSS — дословно прежняя.
CLOSING_RSS = "Смотреть надо тест, названный выше."

# §6: закрывающая строка ветки REASON_FREE.
CLOSING_FREE = "Память съели процессы, названные выше, — тест тут ни при чём."

# §6: заголовок блока.
TOP_HEADER = "съели больше всех:"

# §3, таблица трёх состояний.
WORDING_NOT_SAMPLED = "список процессов не снимался"
WORDING_EMPTY_SWEEP = "перебор ничего не вернул"

# §7 пин 5.
MARK_SELF = "ЭТОТ ПРОГОН"

# §6 + текущий отчёт: поля, которые не имеют права переехать или переименоваться.
LEGACY_LABELS = (
    "РЕМЕНЬ ПО ПАМЯТИ ОСТАНОВИЛ ПРОГОН",
    "  пробито      : ",
    "  тест         : ",
    "  свободно RAM : ",
    "  свой RSS     : ",
    "  от старта    : ",
    "  Это НЕ падение теста. Прогон остановлен, чтобы машина не дошла до",
    "  потолка commit: 21.08 такой прогон кончился BSOD и перезагрузкой.",
)

# Публичные имена модуля ДО DEV-52 — литеральный список, а не интроспекция.
# Нужен двум сторожам: «ничего не переименовали» и «найти НОВУЮ функцию-сборщик,
# не угадывая её имя».
PUBLIC_BEFORE_DEV52 = frozenset({
    "ACTION_OK", "ACTION_INTERRUPT", "ACTION_WAIT", "ACTION_EXIT",
    "REASON_FREE", "REASON_RSS",
    "BELT_LOG_PATH",
    "DEFAULT_KILL_FREE_GB", "DEFAULT_KILL_RSS_GB", "DEFAULT_SAMPLE_S",
    "DEFAULT_HARD_GRACE_S", "DEFAULT_EXIT_CODE",
    "DEFAULT_HARD_RSS_GB", "DEFAULT_HARD_FREE_GB",
    "Limits", "Belt", "Sampler", "CurrentTest", "Installed",
    "limits_from_env", "render_report", "make_emitter", "install",
})

_NUMBERED_LINE = re.compile(r"^\s*\d+\.\s", re.MULTILINE)


def _limits(**kw) -> Limits:
    base = dict(free_gb=2.5, rss_gb=3.0, sample_s=2.0, grace_s=20.0,
                hard_rss_gb=6.0, hard_free_gb=1.0,
                enabled=True, exit_code=77)
    base.update(kw)
    return Limits(**base)


@dataclass(frozen=True)
class _DuckRow:
    """Запасная запись на случай, если реализация назвала тип иначе.

    Сторожа на САМ тип `ProcRow` живёт отдельно (§3). Здесь запасной вариант
    нужен затем, чтобы пины 1–5 краснели по СВОЕЙ причине, а не хором по одному
    отсутствующему имени.
    """
    name: str
    pid: int
    rss_gb: float
    is_self: bool


def _row(name: str, pid: int, rss_gb: float, is_self: bool = False):
    proc_row = getattr(belt_mod, "ProcRow", None)
    if proc_row is None:
        return _DuckRow(name=name, pid=pid, rss_gb=rss_gb, is_self=is_self)
    return proc_row(name=name, pid=pid, rss_gb=rss_gb, is_self=is_self)


def _three_rows():
    """Ровно тот пример, что нарисован в §6."""
    return [
        _row("chrome.exe", 4812, 5.10, False),
        _row("python.exe", 11136, 1.90, False),
        _row("python.exe", 9200, 0.62, True),
    ]


def _report(reason: str, **kw) -> str:
    args = dict(current_test="tests/test_foo.py::test_bar",
                free_gb=2.31, rss_gb=0.62, elapsed_s=512.4,
                limits=_limits(), reason=reason)
    args.update(kw)
    return render_report(**args)


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 1
# ─────────────────────────────────────────────────────────────────────────────


def test_pin1_free_branch_shows_processes_and_drops_the_blame_on_the_test():
    """REASON_FREE + непустой top_rss → блок про процессы есть, а строки
    «Смотреть надо тест, названный выше» НЕТ.

    Свободную память мог съесть кто угодно снаружи: ферма, браузер, второй
    раннер. Названный тест был просто ТЕКУЩИМ — это совпадение по времени,
    предъявленное как причина.
    """
    text = _report(REASON_FREE, top_rss=_three_rows())

    assert TOP_HEADER in text
    assert "chrome.exe" in text
    assert "4812" in text
    assert CLOSING_RSS not in text


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 2
# ─────────────────────────────────────────────────────────────────────────────


def test_pin2_rss_branch_keeps_the_old_closing_line_verbatim():
    """REASON_RSS — память сожрал САМ процесс pytest, и там прежний вывод верен.

    Строка сверяется ДОСЛОВНО: перефразированная закрывающая строка — это
    молчаливая смена смысла отчёта.
    """
    text = _report(REASON_RSS, rss_gb=4.7, top_rss=_three_rows())

    assert CLOSING_RSS in text


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 3
# ─────────────────────────────────────────────────────────────────────────────


def test_pin3_none_says_not_sampled_and_prints_no_numbered_lines():
    """`None` = «список НЕ снимали». Это НЕ то же самое, что пустой список."""
    text = _report(REASON_FREE, top_rss=None)

    assert "не снимался" in text
    assert _NUMBERED_LINE.search(text) is None, (
        "top_rss=None не имеет права печатать нумерованные строки процессов")


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 4
# ─────────────────────────────────────────────────────────────────────────────


def test_pin4_empty_list_says_sweep_returned_nothing_and_differs_from_none():
    """`[]` = «снимали, никого не увидели».

    Склейка с `None` вернула бы ровно ту слепоту, от которой лечимся: у «не
    знаем» и «знаем, что пусто» разные причины и разные следующие действия.
    Поэтому сверяется не только слово, но и РАЗЛИЧИЕ двух отчётов.
    """
    text_empty = _report(REASON_FREE, top_rss=[])
    text_none = _report(REASON_FREE, top_rss=None)

    assert "ничего не вернул" in text_empty
    assert _NUMBERED_LINE.search(text_empty) is None
    assert text_empty != text_none, (
        "None и [] склеены: отчёт одинаков для «не снимали» и «сняли пусто»")


def test_pin4_none_and_empty_do_not_borrow_each_others_wording():
    """Отдельный зуб к пину 4: различие обязано быть по СМЫСЛУ, а не по случайной
    мелочи вроде лишнего пробела."""
    text_empty = _report(REASON_FREE, top_rss=[])
    text_none = _report(REASON_FREE, top_rss=None)

    assert "ничего не вернул" not in text_none
    assert "не снимался" not in text_empty


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 5
# ─────────────────────────────────────────────────────────────────────────────


def test_pin5_self_row_is_marked_in_its_own_line():
    """`is_self` нужен, чтобы читающий не принял собственный pytest за
    пожирателя. Пометка обязана стоять В ТОЙ ЖЕ строке, иначе она указывает не
    на тот процесс."""
    text = _report(REASON_FREE, top_rss=_three_rows())

    self_lines = [ln for ln in text.splitlines() if "9200" in ln]
    assert self_lines, "строки собственного процесса (pid 9200) в отчёте нет"
    assert any(MARK_SELF in ln for ln in self_lines), (
        "у строки с is_self=True нет пометки %r" % MARK_SELF)

    other_lines = [ln for ln in text.splitlines() if "4812" in ln]
    assert other_lines
    assert all(MARK_SELF not in ln for ln in other_lines), (
        "пометка %r стоит у ЧУЖОГО процесса" % MARK_SELF)


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 6 — статически, по исходнику
# ─────────────────────────────────────────────────────────────────────────────


def _module_level_imports(tree: ast.Module):
    """Имена, импортированные на УРОВНЕ МОДУЛЯ.

    Внутрь функций и классов не спускаемся намеренно: импорт psutil внутри
    `install()` — это норма по §4 Г1. Внутрь `if`/`try`/`with` спускаемся:
    `try: import psutil` на верхнем уровне — всё ещё уровень модуля.
    """
    names = []

    def walk(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            if isinstance(node, ast.Import):
                names.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module.split(".")[0])
            for field in ("body", "orelse", "finalbody", "handlers"):
                inner = getattr(node, field, None)
                if isinstance(inner, list):
                    walk([n for n in inner if isinstance(n, ast.stmt)])
                    for h in inner:
                        if isinstance(h, ast.ExceptHandler):
                            walk(h.body)

    walk(tree.body)
    return names


def test_pin6_module_does_not_import_psutil_at_module_level():
    """§4 Г1: `render_report` остаётся ЧИСТОЙ.

    Затащить psutil в чистое ядро = сделать сторожей на отчёт невозможными.
    Проверка СТАТИЧЕСКАЯ: живой импорт мог бы отработать по кэшу sys.modules и
    промолчать.
    """
    path = Path(belt_mod.__file__)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "в .py этого репозитория BOM запрещён — ast.parse на нём краснеет")

    tree = ast.parse(raw.decode("utf-8"))
    imported = _module_level_imports(tree)

    assert "psutil" not in imported, (
        "psutil импортирован на уровне модуля %s: чистое ядро отчёта потеряно "
        "(§4 Г1)" % path)


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 7 — сборщик списка. Имя спекой НЕ задано, поэтому ищем ПО КОНТРАКТУ.
# ─────────────────────────────────────────────────────────────────────────────
#
# 🔴 ДЫРА СПЕКИ №1. §4 Г1 требует, чтобы список снимался «в слое I/O
# (`Sampler`/`install`)», но НЕ называет ни функцию-сборщик, ни её сигнатуру.
# Угадывать имя нельзя: зелёный тест на выдуманное имя — это ложный зелёный.
# Поэтому сборщик разыскивается по контракту: НОВАЯ публичная функция модуля
# (не из литерального списка PUBLIC_BEFORE_DEV52), принимающая источник
# процессов первым параметром и возвращающая список записей ProcRow.
# Спека ОБЯЗАНА дозадать: точное имя, порядок параметров и имя параметра-
# инъекции источника (например `collect_top_rss(process_source=..., limit=3)`).


class _MemInfo:
    def __init__(self, rss: int) -> None:
        self.rss = rss


class _FakeProc:
    """Подставной процесс в форме psutil: `.pid`, `.name()`, `.memory_info()`
    и `.info` — ровно то, что отдаёт `process_iter(["name","pid","memory_info"])`.
    """

    def __init__(self, name: str, pid: int, rss_gb: float, raises=None) -> None:
        self.pid = pid
        self._name = name
        self._rss = int(rss_gb * (2 ** 30))
        self._raises = raises

    @property
    def info(self):
        if self._raises is not None:
            raise self._raises
        return {"name": self._name, "pid": self.pid,
                "memory_info": _MemInfo(self._rss)}

    def name(self):
        if self._raises is not None:
            raise self._raises
        return self._name

    def memory_info(self):
        if self._raises is not None:
            raise self._raises
        return _MemInfo(self._rss)


def _psutil_errors():
    """Классы исключений psutil. Процессы НЕ перебираются — берутся только типы,
    которыми реализация обязана уметь давиться (§5)."""
    import psutil
    return psutil.AccessDenied, psutil.NoSuchProcess


def _collector_candidates():
    out = []
    for name, obj in sorted(vars(belt_mod).items()):
        if name.startswith("_") or name in PUBLIC_BEFORE_DEV52:
            continue
        if not inspect.isfunction(obj):
            continue
        if getattr(obj, "__module__", None) != belt_mod.__name__:
            continue
        params = list(inspect.signature(obj).parameters.values())
        if not params:
            continue
        out.append((name, obj, params))
    return out


def _looks_like_rows(value) -> bool:
    if not isinstance(value, list):
        return False
    return all(hasattr(r, "name") and hasattr(r, "pid")
               and hasattr(r, "rss_gb") and hasattr(r, "is_self")
               for r in value)


def _collect(source):
    """Вызвать сборщик, подсунув ему источник процессов.

    Падает ГРОМКО и по делу, если сборщика нет: «не смогли проверить» не имеет
    права выглядеть как «проверили, всё хорошо» (DEV-43).
    """
    candidates = _collector_candidates()
    if not candidates:
        pytest.fail(
            "в app.services.suite_ram_belt нет НОВОЙ публичной функции-сборщика "
            "топа процессов. §7 пин 7 требует её, §4 Г1 велит держать её в слое "
            "I/O, но спека не задала ни имени, ни сигнатуры. Известные до "
            "DEV-52 публичные имена: %s" % ", ".join(sorted(PUBLIC_BEFORE_DEV52)))

    errors = []
    for name, fn, params in candidates:
        first = params[0]
        if first.kind in (inspect.Parameter.VAR_POSITIONAL,
                          inspect.Parameter.VAR_KEYWORD):
            continue
        try:
            if first.kind is inspect.Parameter.KEYWORD_ONLY:
                result = fn(**{first.name: source})
            else:
                result = fn(source)
        except TypeError as exc:
            errors.append("%s: %s" % (name, exc))
            continue
        if _looks_like_rows(result):
            return result
        errors.append("%s вернула %r — это не список записей ProcRow"
                      % (name, result))

    pytest.fail(
        "ни одна новая публичная функция модуля не приняла подставной источник "
        "процессов первым параметром и не вернула список ProcRow. Пробовали: %s"
        % "; ".join(errors))


def test_pin7_access_denied_on_one_process_does_not_cancel_the_rest():
    """§5: отказ в доступе пропускает ПРОЦЕСС, а не весь перебор.

    Иначе один защищённый системный процесс превращает подсказку в пустоту —
    ровно в аварии, когда она и нужна.
    """
    access_denied, _ = _psutil_errors()
    source = [
        _FakeProc("chrome.exe", 4812, 5.10),
        _FakeProc("System", 4, 0.0, raises=access_denied(pid=4)),
        _FakeProc("python.exe", 11136, 1.90),
    ]

    rows = _collect(source)

    names = [r.name for r in rows]
    assert "chrome.exe" in names
    assert "python.exe" in names
    assert "System" not in names


def test_pin7_failing_sweep_does_not_propagate_out_of_the_collector():
    """§4 Г2: подсказка не имеет права уронить отчёт.

    Перебор при нехватке памяти может упасть целиком (MemoryError, отказ ОС).
    Сборщик обязан съесть это сам: исключение, вылетевшее наружу, убьёт отчёт,
    который и есть весь предмет ремня.
    """
    class _Exploding:
        def __iter__(self):
            raise MemoryError("перебор сорвался")

    try:
        result = _collect(_Exploding())
    except MemoryError as exc:               # noqa: BLE001
        pytest.fail("сборщик выпустил наружу %r — отчёт потерян (§4 Г2)" % (exc,))

    assert result == [] or result is None or _looks_like_rows(result)


def test_pin7_report_stays_complete_when_the_list_is_missing():
    """Вторая половина пина 7: отказ перебора даёт СТРОКУ, а отчёт остаётся
    ПОЛНЫМ — все прежние поля на месте."""
    text = _report(REASON_FREE, top_rss=None)

    for label in LEGACY_LABELS:
        assert label in text, "поле %r пропало из отчёта" % label


# 🔴 ПИН 7в НЕ НАПИСАН: спека не задала канал для ПРИЧИНЫ отказа.
#
# §4 Г2 требует, чтобы отказ перебора давал строку `список процессов не снялся:
# <причина>`. Но §3 задаёт РОВНО ТРИ состояния данных — `None`, `[]`, `[...]` —
# и ни одно из них не несёт текста причины. Под контрактом §3 отказ неотличим от
# «не снимали» и напечатается как `список процессов не снимался`, что §4 Г2
# прямо противоречит.
#
# Чтобы этот пин стал писуемым, спека обязана дозадать ЧЕТВЁРТОЕ состояние или
# отдельный аргумент, например:
#     render_report(..., top_rss=None, top_rss_error: str | None = None)
# и литеральную формулировку строки отказа целиком (сейчас в §4 Г2 дан только
# префикс `список процессов не снялся: `).


# 🔴 ПИН НА БЮДЖЕТ 0.5 с НЕ НАПИСАН: спека не дала точки инъекции часов.
#
# §4 Г3 требует, чтобы перебор укладывался в 0.5 с, а не уложившись — отдавал
# то, что успел, и говорил об этом строкой. Проверить это можно только двумя
# способами: настоящими паузами (медленный и плавающий сторож — то есть будущий
# ложный красный) или инъекцией часов и бюджета в сборщик. Ни имени параметра
# бюджета, ни инъекции часов спека не задаёт. Дозадать обязана:
#     collect_top_rss(source, *, budget_s: float = 0.5, clock=time.monotonic)
# плюс литеральную строку «успели не всех».


# ─────────────────────────────────────────────────────────────────────────────
# §7 пин 8
# ─────────────────────────────────────────────────────────────────────────────


def test_pin8_all_previous_report_fields_are_still_there_untouched():
    """Ни одно существующее поле не переехало и не переименовано.

    Пины прежнего ремня живут в tests/test_suite_ram_belt.py; здесь литеральный
    дубль по РАЗМЕТКЕ отчёта: два числа на одну вещь расходятся молча, а два
    названия одного поля — тем более.
    """
    text = _report(REASON_RSS, rss_gb=4.7, top_rss=_three_rows())

    for label in LEGACY_LABELS:
        assert label in text, "поле %r переехало или переименовано" % label


def test_pin8_render_report_still_works_without_the_new_argument():
    """`top_rss` — именованный аргумент СО ЗНАЧЕНИЕМ ПО УМОЛЧАНИЮ (§3).

    Прежние вызовы обязаны продолжать работать без правок, иначе пин 8 нарушен
    самим фактом появления новой обязаловки.
    """
    text = render_report(current_test="tests/t.py::t", free_gb=1.2, rss_gb=4.7,
                         elapsed_s=931.0, limits=_limits(), reason=REASON_RSS)

    for label in LEGACY_LABELS:
        assert label in text
    assert CLOSING_RSS in text


def test_pin8_public_names_of_the_module_are_not_renamed():
    """Литеральный список против интроспекции: выведенный из кода список
    согласен с кодом по определению."""
    missing = sorted(n for n in PUBLIC_BEFORE_DEV52
                     if not hasattr(belt_mod, n))

    assert missing == [], "публичные имена исчезли или переименованы: %s" % missing


# ─────────────────────────────────────────────────────────────────────────────
# §3: контракт данных
# ─────────────────────────────────────────────────────────────────────────────


def test_procrow_is_an_immutable_record_with_four_fields():
    """§3 называет тип `ProcRow` и требует неизменяемости."""
    proc_row = getattr(belt_mod, "ProcRow", None)
    assert proc_row is not None, "§3 требует тип ProcRow в suite_ram_belt"

    row = proc_row(name="python.exe", pid=9200, rss_gb=0.62, is_self=True)

    assert row.name == "python.exe"
    assert row.pid == 9200
    assert row.rss_gb == 0.62
    assert row.is_self is True

    with pytest.raises(Exception):
        row.pid = 1


# ─────────────────────────────────────────────────────────────────────────────
# §5: что именно снимается
# ─────────────────────────────────────────────────────────────────────────────


def test_collector_sorts_by_rss_descending_and_keeps_exactly_three():
    """§5: сортировка по RSS убыв., срез — первые ТРИ.

    Три, а не пять: отчёт читают в аварии, и он обязан помещаться в экран.
    """
    source = [
        _FakeProc("small.exe", 1, 0.10),
        _FakeProc("huge.exe", 2, 9.00),
        _FakeProc("mid.exe", 3, 3.00),
        _FakeProc("big.exe", 4, 5.00),
        _FakeProc("tiny.exe", 5, 0.01),
    ]

    rows = _collect(source)

    assert [r.name for r in rows] == ["huge.exe", "big.exe", "mid.exe"]


def test_collector_skips_processes_that_vanished_mid_sweep():
    """§5: `NoSuchProcess` — норма, а не отказ. Процесс просто умер между
    перебором и чтением полей."""
    _, no_such = _psutil_errors()
    source = [
        _FakeProc("alive.exe", 1, 2.00),
        _FakeProc("gone.exe", 2, 9.00, raises=no_such(pid=2)),
    ]

    rows = _collect(source)

    assert [r.name for r in rows] == ["alive.exe"]


def test_collector_marks_the_running_pytest_as_self():
    """§3: `is_self` отличает собственный процесс. Без него читающий примет
    свой же pytest за пожирателя."""
    import os

    source = [
        _FakeProc("chrome.exe", 4812, 5.10),
        _FakeProc("python.exe", os.getpid(), 1.90),
    ]

    rows = _collect(source)

    by_pid = {r.pid: r for r in rows}
    assert by_pid[os.getpid()].is_self is True
    assert by_pid[4812].is_self is False


# ─────────────────────────────────────────────────────────────────────────────
# §6: форма в отчёте
# ─────────────────────────────────────────────────────────────────────────────


def test_report_prints_exactly_three_numbered_rows_with_name_pid_and_rss():
    text = _report(REASON_FREE, top_rss=_three_rows())

    numbered = [ln for ln in text.splitlines() if _NUMBERED_LINE.match(ln)]
    assert len(numbered) == 3, "ожидались три нумерованные строки, вышло %d" % len(numbered)

    assert "chrome.exe" in numbered[0]
    assert "4812" in numbered[0]
    assert "5.10" in numbered[0]
    assert "11136" in numbered[1]
    assert "9200" in numbered[2]


def test_free_branch_closing_line_is_the_literal_from_the_spec():
    """§6: закрывающая строка ветки REASON_FREE — дословная."""
    text = _report(REASON_FREE, top_rss=_three_rows())

    assert CLOSING_FREE in text


def test_top_block_header_is_the_literal_from_the_spec():
    text = _report(REASON_FREE, top_rss=_three_rows())

    assert TOP_HEADER in text


def test_none_state_uses_the_wording_from_section_three():
    """§3 таблица даёт формулировку целиком, а не только «не снимался»."""
    text = _report(REASON_FREE, top_rss=None)

    assert WORDING_NOT_SAMPLED in text


def test_empty_state_uses_the_wording_from_section_three():
    text = _report(REASON_FREE, top_rss=[])

    assert WORDING_EMPTY_SWEEP in text
