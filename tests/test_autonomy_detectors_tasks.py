# -*- coding: utf-8 -*-
"""Ш1: два детектора над одним снимком тасков.

Оба ложно-срабатывающих класса взяты из ФАКТИЧЕСКИХ данных фермы 06.08, а не
придуманы:

  * `JarvisSniperDetached` — триггеров нет ВОВСЕ и это решение владельца
    (снайпер отставлен намеренно, RunPod-пивот). Детектор пропавшего триггера
    обязан молчать: «нет триггера» ≠ «дыра», если триггера и не ждали;
  * `JarvisInfraRestartCloudflared` — триггеров нет ПО СПЕКЕ (on-demand /Run,
    зарегистрирован 06.08). Тоже молчание;
  * коды `LastTaskResult`, снятые сегодня с живой фермы: `267011` у свежего
    таска («ни разу не запускался») и `2147946720` у трёх РАБОТАЮЩИХ
    гардианов. Считать их падением — значит завалить теневой набор шумом на
    здоровой системе.
"""
from __future__ import annotations

from app.services import autonomy_detectors as det

# --- реальные формы с фермы ------------------------------------------------
GUARDIAN_OK = det.TaskSnapshot(
    name="JarvisChatterGuardian", state="Running",
    triggers=("BootTrigger", "LogonTrigger", "TimeTrigger"),
    repetition_interval="PT10M", expected_repetition=True,
    last_results=(2147946720, 2147946720))
GUARDIAN_LOST_TIMER = det.TaskSnapshot(
    name="JarvisBotGuardian", state="Running",
    triggers=("BootTrigger", "LogonTrigger"),
    repetition_interval=None, expected_repetition=True,
    last_results=(2147946720,))
SNIPER_RETIRED = det.TaskSnapshot(
    name="JarvisSniperDetached", state="Ready", triggers=(),
    repetition_interval=None, expected_repetition=False,
    last_results=(0,), note="отставлен намеренно (RunPod-пивот)")
ON_DEMAND = det.TaskSnapshot(
    name="JarvisInfraRestartCloudflared", state="Ready", triggers=(),
    repetition_interval=None, expected_repetition=False,
    last_results=(267011,), note="on-demand /Run по спеке")
DAILY_OK = det.TaskSnapshot(
    name="JarvisIgTokenRefresh", state="Ready", triggers=("DailyTrigger",),
    repetition_interval=None, expected_repetition=False, last_results=(0, 0))
FAILING = det.TaskSnapshot(
    name="JarvisStateBackup", state="Ready", triggers=("DailyTrigger",),
    repetition_interval=None, expected_repetition=False, last_results=(1, 1))
FLAKY_ONCE = det.TaskSnapshot(
    name="JarvisErrorDigest", state="Ready", triggers=("DailyTrigger",),
    repetition_interval=None, expected_repetition=False, last_results=(0, 1))


def _snap(tasks):
    return {"tasks": list(tasks)}


# --- детектор 1: пропавший повторяющийся триггер ---------------------------

def test_missing_trigger_fires_on_a_guardian_that_lost_its_timer():
    found = det.detect_missing_trigger(_snap([GUARDIAN_OK, GUARDIAN_LOST_TIMER]))
    assert [p.subject for p in found] == ["JarvisBotGuardian"]
    assert found[0].kind == "task_missing_repetition"
    assert found[0].evidence["triggers"] == ["BootTrigger", "LogonTrigger"]
    assert found[0].proposed_action["action"] == "register_missing_task"


def test_missing_trigger_is_silent_on_a_deliberately_retired_task():
    """Снайпер отставлен решением владельца — «почини» здесь было бы вредом."""
    assert det.detect_missing_trigger(_snap([SNIPER_RETIRED])) == []


def test_missing_trigger_is_silent_on_an_on_demand_task():
    """Аварийная кнопка без триггеров — это спека, а не дыра."""
    assert det.detect_missing_trigger(_snap([ON_DEMAND])) == []


def test_missing_trigger_is_silent_when_the_timer_is_in_place():
    assert det.detect_missing_trigger(_snap([GUARDIAN_OK, DAILY_OK])) == []


def test_missing_trigger_evidence_is_stable_between_runs():
    first = det.detect_missing_trigger(_snap([GUARDIAN_LOST_TIMER]))
    second = det.detect_missing_trigger(_snap([GUARDIAN_LOST_TIMER]))
    assert first[0].evidence == second[0].evidence


# --- детектор 2: два ненулевых кода подряд ---------------------------------

def test_failing_task_fires_after_two_bad_runs():
    found = det.detect_failing_task(_snap([FAILING]))
    assert [p.subject for p in found] == ["JarvisStateBackup"]
    assert found[0].kind == "task_failing_twice"
    assert found[0].evidence["last_results"] == [1, 1]


def test_failing_task_is_silent_after_a_single_bad_run():
    """Один сбой — не тенденция; предложение по нему это шум."""
    assert det.detect_failing_task(_snap([FLAKY_ONCE])) == []


def test_one_failure_alone_in_history_does_not_fire():
    """Пин самого порога «два подряд». Без этого теста мутация
    `len(recent) < 2` -> `< 1` выживала: в паре (0, 1) ноль всё равно
    отсекался фильтром кодов, и порог оставался непроверенным.
    """
    single = det.TaskSnapshot(
        name="JarvisMorningDigest", state="Ready", triggers=("DailyTrigger",),
        repetition_interval=None, expected_repetition=False, last_results=(1,))
    assert det.detect_failing_task(_snap([single])) == []


def test_running_and_never_run_codes_are_not_failures():
    """Коды с живой фермы: 2147946720 у трёх РАБОТАЮЩИХ гардианов и 267011
    у свежего таска. Принять их за падение = зашуметь на здоровой системе."""
    assert det.detect_failing_task(_snap([GUARDIAN_OK, ON_DEMAND])) == []


def test_failing_task_is_silent_without_history():
    empty = det.TaskSnapshot(name="X", state="Ready", triggers=(),
                             repetition_interval=None, expected_repetition=False,
                             last_results=())
    assert det.detect_failing_task(_snap([empty])) == []


def test_drill_refusal_is_reported_but_marked_as_a_refusal():
    """rc=2 у дрила — честный отказ, а не крах. Два отказа подряд стоит
    показать (стенд не прогоняется), но evidence обязан называть вещи
    своими именами, иначе владелец получит ложную панику."""
    drill = det.TaskSnapshot(
        name="JarvisDrillNightly", state="Ready", triggers=("DailyTrigger",),
        repetition_interval=None, expected_repetition=False,
        last_results=(2, 2), refusal_codes=(2,))
    found = det.detect_failing_task(_snap([drill]))
    assert [p.subject for p in found] == ["JarvisDrillNightly"]
    assert found[0].evidence["all_refusals"] is True


def test_both_detectors_stay_pure():
    import ast

    with open(det.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"urllib", "requests", "socket", "sqlite3",
                            "subprocess", "os", "shutil", "pathlib"})
