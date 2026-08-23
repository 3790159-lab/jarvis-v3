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


# ✅ ПИН 7в НАПИСАН: канал причины дозадан АМЕНДМЕНТОМ А
# (`top_rss_error`, приоритет 1 из четырёх). Тесты — в разделе «АМЕНДМЕНТ А»
# в конце файла.


# ✅ ПИН НА БЮДЖЕТ НАПИСАН: часы и бюджет внедряются по АМЕНДМЕНТУ В
# (`clock=`, `budget_s=`, `status["truncated"]`). Настоящих пауз в сторожах нет.
# Тесты — в разделе «АМЕНДМЕНТ В» в конце файла.


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


# ═════════════════════════════════════════════════════════════════════════════
# АМЕНДМЕНТ А. Канал причины отказа. Закрывает пин 7в и замечание №1.
# ═════════════════════════════════════════════════════════════════════════════
#
# Спека дозадана: `render_report` берёт `top_rss_error` и `top_rss_truncated`,
# а порядок разбора СТРОГИЙ — причина отказа старше всех прочих состояний.
# Ниже пины на строку целиком, а не на подстроку: «ровно вида» в спеке — это
# требование к СТРОКЕ, и подстрочная проверка пропустила бы приписку.

# Литеральные строки амендмента А. Отступ в два пробела — часть строки.
LINE_ERROR_PREFIX = "  список процессов не снялся: "
LINE_NOT_SAMPLED = "  список процессов не снимался"
LINE_EMPTY_SWEEP = "  перебор ничего не вернул"
LINE_TOP_HEADER = "  съели больше всех:"


def _has_line(text: str, expected: str) -> bool:
    return any(ln == expected for ln in text.splitlines())


def _lines_hint(text: str) -> str:
    return "\n".join("    %r" % ln for ln in text.splitlines())


def test_amendmentA_failure_reason_is_a_line_of_exactly_the_specified_shape():
    """Приоритет 1: строка ровно `"  список процессов не снялся: " + причина`.

    Причина обязана ДОЕХАТЬ до глаз. Отказ, напечатанный без текста причины,
    ничем не лучше молчания: читающий в аварии не станет лезть в код за тем,
    какая именно ветка отказа сработала.
    """
    reason = "MemoryError при переборе процессов"
    text = _report(REASON_FREE, top_rss=None, top_rss_error=reason)

    assert _has_line(text, LINE_ERROR_PREFIX + reason), (
        "строки %r в отчёте нет. Строки отчёта:\n%s"
        % (LINE_ERROR_PREFIX + reason, _lines_hint(text)))


def test_amendmentA_reason_text_is_carried_verbatim_not_summarised():
    """Второй зуб к тому же: текст причины не имеет права быть подменён
    обобщением. Два разных отказа обязаны читаться как два разных отказа."""
    first = _report(REASON_FREE, top_rss=None, top_rss_error="отказано в доступе")
    second = _report(REASON_FREE, top_rss=None, top_rss_error="перебор сорвался")

    assert _has_line(first, LINE_ERROR_PREFIX + "отказано в доступе")
    assert _has_line(second, LINE_ERROR_PREFIX + "перебор сорвался")
    assert first != second, (
        "два разных текста причины дали ОДИН отчёт: причина потеряна по дороге")


def test_amendmentA_error_outranks_the_not_sampled_state():
    """Приоритет 1 выше приоритета 2. Ровно тот случай, ради которого амендмент
    и писался: `top_rss=None` вместе с причиной — это ОТКАЗ, а не «не снимали».
    """
    text = _report(REASON_FREE, top_rss=None, top_rss_error="перебор сорвался")

    assert LINE_NOT_SAMPLED not in text, (
        "при заданном top_rss_error отчёт всё равно сказал «не снимался» — "
        "отказ склеен с «не снимали», то есть причина проглочена")
    assert _has_line(text, LINE_ERROR_PREFIX + "перебор сорвался")


def test_amendmentA_error_outranks_the_empty_and_the_filled_list_too():
    """«СТРОГО такой» порядок — значит причина старше и пустого списка, и
    непустого. Иначе половина исходов печатала бы отказ, а половина — нет."""
    text_empty = _report(REASON_FREE, top_rss=[], top_rss_error="перебор сорвался")
    text_rows = _report(REASON_FREE, top_rss=_three_rows(),
                        top_rss_error="перебор сорвался")

    assert LINE_EMPTY_SWEEP not in text_empty
    assert _has_line(text_empty, LINE_ERROR_PREFIX + "перебор сорвался")

    assert LINE_TOP_HEADER not in text_rows
    assert _NUMBERED_LINE.search(text_rows) is None, (
        "top_rss_error задан, но отчёт всё равно печатает нумерованный список: "
        "приоритет 1 не соблюдён")


def test_amendmentA_report_stays_complete_when_the_sweep_failed():
    """«Отчёт при любом из четырёх исходов остаётся ПОЛНЫМ.»

    Отказ подсказки не имеет права утащить за собой сам отчёт — ремень ради
    отчёта и существует.
    """
    text = _report(REASON_FREE, top_rss=None, top_rss_error="перебор сорвался")

    for label in LEGACY_LABELS:
        assert label in text, "поле %r пропало из отчёта при отказе перебора" % label
    assert CLOSING_FREE in text


def test_amendmentA_truncated_flag_does_not_downgrade_a_delivered_list():
    """`top_rss_truncated` НЕ участвует в разборе четырёх исходов: при непустом
    списке и пустой причине действует исход 4.

    Пин выведен из порядка «СТРОГО такой» и ни из чего больше: литеральной
    формулировки для урезанного списка спека не дала (см. недосказанность №5
    в конце файла), поэтому текста про урезание тут не проверяется.
    """
    text = _report(REASON_FREE, top_rss=_three_rows(), top_rss_truncated=True)

    assert _has_line(text, LINE_TOP_HEADER)
    numbered = [ln for ln in text.splitlines() if _NUMBERED_LINE.match(ln)]
    assert len(numbered) == 3
    for label in LEGACY_LABELS:
        assert label in text


def test_amendmentA_new_arguments_all_have_defaults():
    """Все три новых аргумента — именованные СО ЗНАЧЕНИЕМ ПО УМОЛЧАНИЮ.

    Литерально, а не интроспекцией «есть ли параметр»: обязательный аргумент,
    просочившийся в `render_report`, ломает всех прежних звонящих разом.
    """
    sig = inspect.signature(render_report)
    expected = {"top_rss": None, "top_rss_error": None, "top_rss_truncated": False}

    for name, default in expected.items():
        assert name in sig.parameters, (
            "амендмент А требует аргумент %r у render_report" % name)
        param = sig.parameters[name]
        assert param.default == default, (
            "у %r умолчание %r, а амендмент А требует %r"
            % (name, param.default, default))


# ═════════════════════════════════════════════════════════════════════════════
# АМЕНДМЕНТ Б. Имена и сигнатуры. Закрывает замечания №2 и №4.
# ═════════════════════════════════════════════════════════════════════════════
#
# Теперь имена заданы спекой, и разыскивать сборщик «по контракту» больше не
# нужно: пины ниже зовут `top_rss_from` и `collect_top_rss` ПО ИМЕНИ. Пины 7 и
# §5 выше оставлены как есть — они ловят ту же функцию другим способом, и если
# два способа разойдутся, это само по себе сигнал.


def _named(fn_name: str):
    fn = getattr(belt_mod, fn_name, None)
    if fn is None:
        pytest.fail(
            "амендмент Б требует публичную функцию %s в app.services.suite_ram_belt, "
            "её нет" % fn_name)
    return fn


def test_amendmentB_top_rss_from_signature_is_literally_as_specified():
    """Сигнатура — часть контракта, а не деталь.

    Именно из-за неназванных имён параметров пин на бюджет был непишуем.
    Литеральный список против интроспекции: сверяем ИМЕНА и УМОЛЧАНИЯ.
    """
    fn = _named("top_rss_from")
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())

    assert params and params[0].name == "processes", (
        "первый параметр top_rss_from обязан называться `processes`, а не %r"
        % (params[0].name if params else None))

    expected_kw = {"self_pid": None, "clock": None, "count": 3,
                   "budget_s": 0.5, "status": None}
    for name, default in expected_kw.items():
        assert name in sig.parameters, (
            "у top_rss_from нет именованного параметра %r" % name)
        param = sig.parameters[name]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            "%r обязан быть ТОЛЬКО именованным (после `*`), а он %s"
            % (name, param.kind))
        assert param.default == default, (
            "умолчание %r = %r, а спека требует %r"
            % (name, param.default, default))


def test_amendmentB_collect_top_rss_takes_a_callable_named_process_iter():
    fn = _named("collect_top_rss")
    params = list(inspect.signature(fn).parameters.values())

    assert params and params[0].name == "process_iter", (
        "первый параметр collect_top_rss обязан называться `process_iter`, а не %r"
        % (params[0].name if params else None))


def test_amendmentB_sampler_accepts_the_optional_collect_top():
    """Шов из замечания №4 назван: `Sampler(collect_top=...)`, умолчание None."""
    sampler_cls = getattr(belt_mod, "Sampler", None)
    assert sampler_cls is not None
    sig = inspect.signature(sampler_cls.__init__)

    assert "collect_top" in sig.parameters, (
        "амендмент Б требует необязательный параметр `collect_top` у Sampler.__init__")
    assert sig.parameters["collect_top"].default is None, (
        "умолчание `collect_top` обязано быть None — «отчёт печатает состояние "
        "не снимался»")


def test_amendmentB_collect_top_rss_returns_the_three_states_as_one_tuple():
    """Чистый перебор: (строки, None, False)."""
    fn = _named("collect_top_rss")
    procs = [
        _FakeProc("huge.exe", 2, 9.00),
        _FakeProc("big.exe", 4, 5.00),
        _FakeProc("mid.exe", 3, 3.00),
        _FakeProc("tiny.exe", 5, 0.01),
    ]

    rows, error, truncated = fn(lambda: list(procs))

    assert error is None, "чистый перебор не имеет права выдумать причину отказа"
    assert truncated is False, (
        "третье значение кортежа объявлено как bool, пришло %r" % (truncated,))
    assert _looks_like_rows(rows)
    assert [r.name for r in rows] == ["huge.exe", "big.exe", "mid.exe"]


def test_amendmentB_broken_sweep_returns_none_and_not_the_gathered_half():
    """«Оборвавшийся перебор отдаёт rows=None, а НЕ собранную половину.»

    Половина опаснее пустоты: она выглядит как полный ответ. Читающий увидит
    `chrome.exe 5.1 ГБ` первым номером и решит, что виноват браузер, — хотя
    настоящий пожиратель мог не дожить до конца оборванного перебора.
    """
    fn = _named("collect_top_rss")

    def process_iter():
        def gen():
            yield _FakeProc("chrome.exe", 4812, 5.10)
            yield _FakeProc("python.exe", 11136, 1.90)
            raise MemoryError("перебор сорвался на третьем")
        return gen()

    rows, error, truncated = fn(process_iter)

    assert rows is None, (
        "оборванный перебор выдал %r — собранная половина предъявлена как "
        "полный список" % (rows,))
    assert isinstance(error, str) and error, (
        "оборванный перебор обязан назвать причину, пришло %r" % (error,))


def test_amendmentB_collector_does_not_let_the_failure_escape():
    """§4 Г2 на новом имени: наружу летит КОРТЕЖ, а не исключение."""
    fn = _named("collect_top_rss")

    def process_iter():
        raise OSError("процессы не перечислить")

    try:
        rows, error, truncated = fn(process_iter)
    except BaseException as exc:                     # noqa: BLE001
        pytest.fail("collect_top_rss выпустил наружу %r — отчёт потерян" % (exc,))

    assert rows is None
    assert isinstance(error, str) and error


def test_amendmentB_top_rss_from_never_raises_and_writes_the_reason_to_status():
    """«НИКОГДА не бросает», а обстоятельства кладёт в `status`.

    Именно этим каналом отказ и доезжает до `collect_top_rss`, а оттуда до
    строки отчёта. Если `status["error"]` не заполняется, причина умирает молча
    на первом же шве.
    """
    fn = _named("top_rss_from")

    class _Exploding:
        def __iter__(self):
            raise MemoryError("перебор сорвался")

    status = {}
    try:
        rows = fn(_Exploding(), status=status)
    except BaseException as exc:                     # noqa: BLE001
        pytest.fail("top_rss_from бросил %r, а спека говорит НИКОГДА" % (exc,))

    assert isinstance(rows, list), (
        "top_rss_from объявлен как -> list[ProcRow], вернул %r" % (rows,))
    assert isinstance(status.get("error"), str) and status["error"], (
        "обстоятельства отказа не записаны в status['error']: %r" % (status,))


def test_amendmentB_top_rss_from_marks_self_by_the_injected_pid():
    """`self_pid` внедряется, а не берётся из `os.getpid()` внутри.

    Иначе пометка «ЭТОТ ПРОГОН» непроверяема нигде, кроме как на живом pytest,
    и живёт на честном слове.
    """
    fn = _named("top_rss_from")
    procs = [_FakeProc("chrome.exe", 4812, 5.10), _FakeProc("python.exe", 9200, 1.90)]

    rows = fn(procs, self_pid=9200)

    by_pid = {r.pid: r for r in rows}
    assert by_pid[9200].is_self is True
    assert by_pid[4812].is_self is False


# ═════════════════════════════════════════════════════════════════════════════
# АМЕНДМЕНТ В. Бюджет — инъекцией часов. Закрывает замечание №3.
# ═════════════════════════════════════════════════════════════════════════════
#
# Настоящих пауз здесь нет и быть не должно: сторож на `time.sleep(0.5)` — это
# заготовка будущего ложного красного на загруженной машине.
#
# Часы двигает САМ ИСТОЧНИК, по шагу на каждый выданный процесс. Так сторож не
# зависит от того, сколько раз реализация дёрнет `clock()` за оборот: спека
# задала бюджет и поведение при исчерпании, а не число замеров.


class _HandClock:
    """Часы, которые сами не идут. Двигает их источник процессов."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now


class _TimedSweep:
    """Источник, где КАЖДЫЙ выданный процесс стоит `step` секунд.

    Помнит, до кого перебор дошёл: «прекратился досрочно» — это про то, что
    хвост источника ОСТАЛСЯ НЕТРОНУТЫМ, а не про длину результата (результат
    и так срезан до трёх).
    """

    def __init__(self, clock: _HandClock, procs, step: float) -> None:
        self.clock = clock
        self.procs = list(procs)
        self.step = step
        self.consumed = []

    def __iter__(self):
        for proc in self.procs:
            self.clock.now += self.step
            self.consumed.append(proc.pid)
            yield proc


def _six_procs():
    return [_FakeProc("p%d.exe" % i, 100 + i, 9.0 - i * 0.5) for i in range(6)]


def test_amendmentV_exhausted_budget_stops_the_sweep_before_the_tail():
    """Бюджет исчерпан → перебор ПРЕКРАЩАЕТСЯ, хвост источника не тронут."""
    fn = _named("top_rss_from")
    clock = _HandClock()
    sweep = _TimedSweep(clock, _six_procs(), step=0.3)

    fn(sweep, clock=clock, budget_s=0.5, status={})

    assert len(sweep.consumed) < 6, (
        "бюджет 0.5 с при шаге 0.3 с исчерпан на втором процессе, а перебор "
        "прошёл всех шестерых: часы не смотрят или бюджет не соблюдается")


def test_amendmentV_exhausted_budget_sets_truncated_and_hands_over_what_it_got():
    """«собранное отдаётся», и об урезании СКАЗАНО в status.

    Молчаливое урезание — это ложь ровно того сорта, от которой лечится весь
    DEV-52: короткий список выглядит как полный ответ.
    """
    fn = _named("top_rss_from")
    clock = _HandClock()
    sweep = _TimedSweep(clock, _six_procs(), step=0.3)
    status = {}

    rows = fn(sweep, clock=clock, budget_s=0.5, status=status)

    assert status.get("truncated") is True, (
        "перебор оборвался по бюджету, а status['truncated'] = %r"
        % (status.get("truncated"),))
    assert _looks_like_rows(rows) and rows, (
        "собранное не отдано: %r" % (rows,))
    assert set(r.pid for r in rows) <= set(sweep.consumed), (
        "в результате процессы, до которых перебор не доходил: %r против %r"
        % ([r.pid for r in rows], sweep.consumed))
    assert rows[0].name == "p0.exe"


def test_amendmentV_generous_budget_sweeps_everyone_and_flags_nothing():
    """Обратная сторона: пока бюджета хватает, `truncated` не выставляется.

    Сигнал, всегда включённый, — это не сторож, а фон.
    """
    fn = _named("top_rss_from")
    clock = _HandClock()
    sweep = _TimedSweep(clock, _six_procs(), step=0.3)
    status = {}

    rows = fn(sweep, clock=clock, budget_s=100.0, status=status)

    assert sweep.consumed == [100 + i for i in range(6)], (
        "щедрый бюджет, а перебор всё равно оборван: дошёл до %r" % (sweep.consumed,))
    assert not status.get("truncated"), (
        "урезания не было, а status['truncated'] = %r" % (status.get("truncated"),))
    assert [r.name for r in rows] == ["p0.exe", "p1.exe", "p2.exe"]


def test_amendmentV_truncation_survives_the_trip_through_collect_top_rss():
    """Третье значение кортежа — не украшение: урезание обязано доехать до
    отчёта, иначе шов съедает ровно тот факт, ради которого флаг заведён."""
    fn = _named("collect_top_rss")
    clock = _HandClock()
    sweep = _TimedSweep(clock, _six_procs(), step=0.3)

    rows, error, truncated = fn(lambda: sweep, clock=clock, budget_s=0.5)

    assert truncated is True, (
        "урезание по бюджету не доехало до кортежа: truncated = %r" % (truncated,))
    assert error is None, (
        "урезание — не отказ: собранное отдано, причины быть не должно, "
        "пришло %r" % (error,))
    assert _looks_like_rows(rows) and rows, (
        "урезанный перебор обязан отдать собранное, пришло %r" % (rows,))


# ═════════════════════════════════════════════════════════════════════════════
# ШОВ Sampler -> отчёт. Замечание №4.
# ═════════════════════════════════════════════════════════════════════════════
#
# Пины выше проверяют два КОНЦА: сборщик отдаёт кортеж, отчёт печатает строку.
# Между ними шов, на котором всё это может потеряться целиком — и оба конца
# останутся зелёными. Ровно так и выглядит ложный зелёный из-за границы слоя.

_UNSET = object()


def _sampler_report(collect_top=_UNSET, current_test="tests/t.py::test_leak") -> str:
    """Собрать `Sampler` с подставной обвязкой, пробить порог и вернуть отчёт.

    Обвязка — как в tests/test_suite_ram_belt.py: read/clock/emit/interrupt/die/
    current_test внедряются, живых процессов и настоящей аварии нет.
    Порог: free=1.0 ровно на жёсткой линии → мягкий путь и отчёт (REASON_FREE).
    """
    from app.services.suite_ram_belt import Belt, Sampler

    emitted = []
    clock = iter([0.0])
    values = iter([(1.0, 0.8)])
    kwargs = dict(
        read=lambda: next(values),
        clock=lambda: next(clock),
        emit=emitted.append,
        interrupt=lambda: None,
        die=lambda code: None,
        current_test=lambda: current_test,
        started_at=0.0,
    )
    if collect_top is not _UNSET:
        kwargs["collect_top"] = collect_top

    try:
        sampler = Sampler(Belt(_limits()), **kwargs)
    except TypeError as exc:
        pytest.fail(
            "Sampler не собрался с %s: %s"
            % ("collect_top" if collect_top is not _UNSET else "прежней обвязкой", exc))

    sampler.tick()

    assert emitted, "сэмплер пробил порог, но отчёта не выдал"
    return emitted[-1]


def test_seam_sampler_carries_the_collected_rows_into_the_report():
    """Кортеж (строки, None, False) обязан доехать до блока процессов."""
    rows = _three_rows()

    text = _sampler_report(collect_top=lambda: (rows, None, False))

    assert _has_line(text, LINE_TOP_HEADER), (
        "Sampler получил список процессов, но отчёт его не показал. Строки:\n%s"
        % _lines_hint(text))
    assert "chrome.exe" in text
    assert "4812" in text


def test_seam_sampler_carries_the_failure_reason_into_the_report():
    """Кортеж (None, причина, False) обязан доехать до строки отказа.

    Это тот же путь, но по ветке отказа: сборщик мог честно назвать причину, а
    шов — превратить её в «не снимался».
    """
    text = _sampler_report(collect_top=lambda: (None, "перебор сорвался", False))

    assert _has_line(text, LINE_ERROR_PREFIX + "перебор сорвался"), (
        "причина не доехала от collect_top до отчёта. Строки:\n%s" % _lines_hint(text))
    assert LINE_NOT_SAMPLED not in text


def test_seam_sampler_without_collect_top_reports_the_not_sampled_state():
    """«Без него отчёт печатает состояние "не снимался".»

    Не пустой список и не отказ: не звали — значит не знаем.
    """
    text = _sampler_report()

    assert _has_line(text, LINE_NOT_SAMPLED), (
        "без collect_top отчёт обязан сказать %r. Строки:\n%s"
        % (LINE_NOT_SAMPLED, _lines_hint(text)))
    assert _NUMBERED_LINE.search(text) is None
    assert LINE_ERROR_PREFIX.strip() not in text


def test_seam_sampler_does_not_die_when_collect_top_itself_explodes():
    """Шов обязан пережить сборщик, который сам сломался.

    §4 Г2 требует, чтобы подсказка не уронила отчёт; `collect_top` внедряется
    снаружи, и его исключение — такая же подсказка, как оборванный перебор.
    Выведено из Г2, спека про этот случай прямо не говорит (недосказанность №4).
    """
    def _boom():
        raise RuntimeError("сборщик сломался")

    try:
        text = _sampler_report(collect_top=_boom)
    except RuntimeError as exc:                      # noqa: BLE001
        pytest.fail("исключение сборщика вылетело наружу и убило отчёт: %r" % (exc,))

    for label in LEGACY_LABELS:
        assert label in text, "поле %r пропало из отчёта" % label


# ═════════════════════════════════════════════════════════════════════════════
# 🔴 ГДЕ КОНТРАКТ ВСЁ ЕЩЁ МОЛЧИТ (новые места, найдены при написании пинов)
# ═════════════════════════════════════════════════════════════════════════════
#
# №4. `collect_top` у `Sampler` — вызываемое, ВНЕДРЯЕМОЕ снаружи, и спека не
#     говорит, что делать, если оно само бросит. Пин
#     `test_seam_sampler_does_not_die_when_collect_top_itself_explodes` выведен
#     из §4 Г2 по смыслу; спека обязана сказать это прямо (проглотить и напечатать
#     причину — или пусть падает).
#
# №5. `top_rss_truncated=True` принят `render_report`, но НИ ОДНОГО слова о том,
#     что при этом печатается, амендмент А не дал: все четыре исхода разбирают
#     только `top_rss_error` и `top_rss`. Флаг, ничего не меняющий в выводе, —
#     это мёртвый параметр; урезание останется невидимым читающему, ради которого
#     весь отчёт и пишется. Спека обязана дать ЛИТЕРАЛЬНУЮ формулировку
#     (например `  успели не всех: бюджет 0.5 с исчерпан`) и место строки
#     относительно нумерованного списка.
#
# №6. `budget_s` объявлен и у `top_rss_from`, и (через `**kwargs`) у
#     `collect_top_rss`, но не сказано, ЧТО замеряется: только перебор процессов
#     или ещё и сортировка со срезом. При исчерпании бюджета ровно на границе
#     (`elapsed == budget_s`) поведение тоже не задано — пины выше намеренно
#     держатся далеко от границы, чтобы не пинить угаданное.
#
# №7. `status` у `top_rss_from` — «необязательный словарь». Не сказано, обязана
#     ли реализация класть `"truncated": False` при чистом переборе, или ключ
#     просто отсутствует. Пин
#     `test_amendmentV_generous_budget_sweeps_everyone_and_flags_nothing`
#     принимает ОБА варианта (`not status.get("truncated")`) — сузить его можно
#     только после ответа спеки.
#
# №8. Не сказано, что происходит при `top_rss_error` НЕ строкой (например
#     исключение положили в аргумент как есть). Форматирование `str(exc)` против
#     `repr(exc)` меняет читаемость строки отказа, а сверяется она дословно.
#
# №9. КТО ПОДАЁТ `self_pid` — не сказано, и это уже СТОЛКНУЛОСЬ с пином.
#     У `top_rss_from` умолчание `self_pid=None`, то есть «своего не помечать».
#     Значит пометку «ЭТОТ ПРОГОН» обязан обеспечивать кто-то выше: либо
#     `collect_top_rss` сама подставляет `os.getpid()`, либо это делает
#     `install()`. Пока спека молчит, ПРЕЖНИЙ пин
#     `test_collector_marks_the_running_pytest_as_self` (он разыскивает сборщик
#     по контракту и попадает на `top_rss_from`, зовя её ОДНИМ позиционным
#     аргументом) обязан краснеть даже на верной реализации: без `self_pid`
#     пометки не будет. Проверено на черновике-подделке по амендментам: 44 пина
#     из 45 зелёные, красный ровно этот. Спека обязана сказать, кто подставляет
#     свой pid, — иначе либо пин 5 недостижим, либо умолчание должно быть не None.
