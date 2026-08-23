# -*- coding: utf-8 -*-
"""Сторожа DEV-59, часть 4: ЧИСТЫЕ функции `scripts/task_encoding_report.py`.

Контракт §8. Пинятся ТОЛЬКО `format_verdicts` и `exit_code`.
`collect_snapshot` и `read_script_bytes` НЕ пинятся: они импурны и живут за
границей — «модуль не отвечает за то, что читатель принесёт тот же файл,
который реально исполняет Планировщик». Их существование и форма вызова здесь
всё-таки сверяются СТАТИЧЕСКИ (по AST, без импорта и без вызова), потому что
пропавшее имя обязано краснеть, а не тихо не проверяться.

Написано ОТ СПЕКИ И КОНТРАКТА, БЕЗ просмотра реализации.

Ключевой пин файла — `exit_code([]) == 1`: сверка, не увидевшая ни одной
задачи, не «прошла», а НЕ СОСТОЯЛАСЬ. Ровно этот класс ошибки («не смогли
проверить» выглядит как «проверили, всё хорошо») уже стоил молчаливого
сигнала.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from app.services.task_encoding import TaskVerdict


REPO = Path(__file__).resolve().parents[1]
REPORT = REPO / "scripts" / "task_encoding_report.py"

REQUIRED_NAMES = [
    "collect_snapshot", "read_script_bytes",
    "format_verdicts", "exit_code", "main",
]

GREEN_STATES = ("ok", "exempt")
RED_STATES = ("unprotected", "missing", "unexpected", "unreadable",
              "kind_mismatch")


def _source():
    if not REPORT.exists():
        pytest.fail("контракт DEV-59 §8: нет файла %s" % REPORT)
    return REPORT.read_bytes()


def _module():
    raw = _source()
    spec = importlib.util.spec_from_file_location(
        "dev59_task_encoding_report_under_test", REPORT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert raw  # источник прочитан, модуль загружен из него же
    return m


def _need(name):
    m = _module()
    fn = getattr(m, name, None)
    if fn is None:
        pytest.fail("контракт DEV-59 §8: в scripts/task_encoding_report.py "
                    "нет `%s`" % name)
    return fn


def _v(task, state, reason=None):
    return TaskVerdict(task=task, state=state, reason=reason or state)


# ─────────────────────────────────────────────────────────────────────────────
# §8 — файл и его имена
# ─────────────────────────────────────────────────────────────────────────────

def test_report_file_has_no_bom():
    """«файл `.py` — БЕЗ BOM (с BOM `ast.parse` краснеет)» (§8).

    Это уже записанный урок: `.ps1` без BOM обязателен, `.py` с BOM — запрещён.
    """
    raw = _source()
    assert not raw.startswith(b"\xef\xbb\xbf"), "у отчётного скрипта UTF-8 BOM"


def test_report_file_parses():
    ast.parse(_source().decode("utf-8"))


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_report_defines_the_required_name(name):
    """Статически, по AST: имя определено. Импурные не зовутся."""
    tree = ast.parse(_source().decode("utf-8"))
    defined = [n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert name in defined, "имя `%s` не определено" % name


def test_main_takes_argv_with_a_default():
    """`def main(argv=None) -> int` (§8). Статически, без вызова."""
    tree = ast.parse(_source().decode("utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    if fn is None:
        pytest.fail("контракт §8: в отчётном скрипте нет `main`")
    args = [a.arg for a in fn.args.args]
    assert args[:1] == ["argv"], "у `main` первый параметр обязан быть `argv`"
    assert len(fn.args.defaults) >= 1, "`argv` обязан иметь умолчание `None`"


# ─────────────────────────────────────────────────────────────────────────────
# §8 — exit_code. НОЛЬ тогда и только тогда, когда всё в {ok, exempt}
# ─────────────────────────────────────────────────────────────────────────────

def test_exit_code_zero_on_all_green():
    fn = _need("exit_code")
    assert fn([_v("A", "ok"), _v("B", "exempt"), _v("C", "ok")]) == 0


@pytest.mark.parametrize("state", GREEN_STATES)
def test_exit_code_zero_on_a_single_green(state):
    assert _need("exit_code")([_v("A", state)]) == 0


@pytest.mark.parametrize("state", RED_STATES)
def test_exit_code_one_on_each_of_the_five_reds(state):
    """«Любое из пяти красных -> `1`». Каждое красное — своим пином: одно
    забытое состояние и есть та дырка, через которую сверка станет зелёной."""
    fn = _need("exit_code")
    assert fn([_v("A", state)]) == 1


@pytest.mark.parametrize("state", RED_STATES)
def test_one_red_among_greens_still_gives_one(state):
    fn = _need("exit_code")
    verdicts = [_v("A", "ok"), _v("B", "exempt"), _v("C", state),
                _v("D", "ok")]
    assert fn(verdicts) == 1


def test_exit_code_on_an_empty_list_is_one():
    """«Пустой список вердиктов -> `1`»: сверка, не увидевшая ни одной задачи,
    не «прошла», а НЕ СОСТОЯЛАСЬ (§8, ловушка №5)."""
    assert _need("exit_code")([]) == 1


def test_exit_code_looks_at_state_not_at_reason():
    """`declared_late` живёт под `unprotected` — красное по СОСТОЯНИЮ."""
    fn = _need("exit_code")
    assert fn([_v("A", "unprotected", "declared_late")]) == 1
    assert fn([_v("A", "unreadable", "no_reader")]) == 1
    assert fn([_v("A", "unreadable", "exempt_without_reason")]) == 1


def test_exit_code_returns_an_int():
    out = _need("exit_code")([_v("A", "ok")])
    assert isinstance(out, int) and not isinstance(out, bool)


# ─────────────────────────────────────────────────────────────────────────────
# §8 — format_verdicts. По строке на вердикт, порядок сохранён
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE = [
    _v("JarvisStateBackup", "ok"),
    _v("JarvisOpsWatchdog", "unprotected", "declared_late"),
    _v("JarvisBotGuardian", "unreadable", "reader_failed"),
    _v("JarvisIgTokenRefresh", "exempt"),
    _v("JarvisSniperDetached", "kind_mismatch"),
]


def test_format_verdicts_gives_one_line_per_verdict():
    lines = _need("format_verdicts")(SAMPLE)
    assert isinstance(lines, list)
    assert len(lines) == len(SAMPLE)
    assert all(isinstance(x, str) for x in lines)


@pytest.mark.parametrize("index", range(len(SAMPLE)))
def test_each_line_carries_task_state_and_reason(index):
    """«в строке ОБЯЗАНЫ быть имя задачи, состояние и `reason`» (§8).

    Без `reason` в строке дедуп по причине не увидит смены одного красного на
    другое: `unprotected` и `declared_late` чинят РАЗНЫМ действием.
    """
    v = SAMPLE[index]
    line = _need("format_verdicts")(SAMPLE)[index]
    assert v.task in line, "в строке нет имени задачи: %r" % (line,)
    assert v.state in line, "в строке нет состояния: %r" % (line,)
    assert v.reason in line, "в строке нет причины: %r" % (line,)


def test_format_verdicts_keeps_the_order():
    """«Порядок строк = порядок вердиктов» (§8).

    Пинится строго: в i-й строке стоит i-е имя и НЕ стоит ни одно чужое.
    """
    reversed_sample = list(reversed(SAMPLE))
    lines = _need("format_verdicts")(reversed_sample)
    for i, v in enumerate(reversed_sample):
        assert v.task in lines[i]
        for other in reversed_sample:
            if other.task != v.task:
                assert other.task not in lines[i], (
                    "строка %d несёт чужое имя: %r" % (i, lines[i]))


def test_format_verdicts_on_an_empty_list():
    assert _need("format_verdicts")([]) == []


def test_format_verdicts_distinguishes_two_reds_of_one_state():
    """Два `unprotected` с разными причинами обязаны дать РАЗНЫЕ строки."""
    fmt = _need("format_verdicts")
    lines = fmt([_v("A", "unprotected", "unprotected"),
                 _v("A", "unprotected", "declared_late")])
    assert lines[0] != lines[1]
