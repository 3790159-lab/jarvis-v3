"""Кого ферма считает живым: запуск против упоминания.

Живой скриншот 14.08: панель отдала страницу ИЗ ПРОЦЕССА `run_backend_detached`
и на этой же странице написала «ЗБІЙ backend :8010 — процес не знайдено». Ответ,
отрицающий процесс, который его печатает, ложен на самом верхнем уровне — и
никакой сторож этого не ловил, потому что тестов на сам матчер не было вовсе.

Корень — ГРАБЛЯ 1 в прежней редакции: чтобы фильтр не поймал сам себя, из
выдачи выбрасывались собственный PID и ВСЕ предки. Пока сборщик был отдельным
процессом, это работало. Как только панель переехала внутрь бэкенда, под нож
попали разом:
  · сам бэкенд (`os.getpid()`),
  · его родитель — второй PID той же службы,
  · его дед — `backend_guardian_detached.ps1`, то есть гардиан стал невидим.

Замена — структурная: маркер обязан стоять в аргументах ЗАПУСКА (путь скрипта,
имя модуля после -m), а не где угодно в командной строке. Процесс, который
маркер лишь УПОМИНАЕТ внутри `-c` / `-Command`, теперь не совпадает ни у кого, а
не только у собственных потомков. Пара тестов ниже держит обе стороны: живое
видно, упоминание не считается.
"""
from __future__ import annotations

import os

import psutil
import pytest

from app.services import jarvis_farm as F

PY = r"C:\jarvis\.venv\Scripts\python.exe"
PS = "powershell.exe"


def _proc(pid, name, argv, started=1.0, ppid=1):
    """Строка таблицы процессов ровно в той форме, в какой её отдаёт
    `_proc_table`: аргументы СПИСКОМ, потому что граница «флаг / тело inline-кода»
    существует только в токенах — в склеенной строке её уже нет."""
    return (pid, name, argv, started, ppid)


def _by_key(rows):
    return {r.key: r for r in rows}


# ───────────────────── живое обязано быть видно ──────────────────────────────

def test_the_process_that_serves_the_panel_is_never_reported_missing():
    """ГЛАВНЫЙ инвариант. Панель отвечает из `run_backend_detached.py`; строка
    «процесс не найден» о самой себе — ложь, которую видно на первом экране."""
    table = [_proc(os.getpid(), "python.exe",
                   [PY, r"C:\jarvis\scripts\run_backend_detached.py"])]
    row = _by_key(F.processes(table))["backend"]
    assert row.state == "ok", f"панель отрицает собственный процесс: {row.detail}"
    assert str(os.getpid()) in row.detail, row.detail


def test_the_second_pid_of_the_same_service_is_counted_too():
    """У бэкенда два PID (лаунчер + сам сервис) — это норма, а не двойник.
    Родитель тоже обязан попасть в выдачу: раньше его срезало как «предка»."""
    me = os.getpid()
    parent = psutil.Process(me).ppid()
    table = [_proc(me, "python.exe", [PY, r"C:\jarvis\scripts\run_backend_detached.py"],
                   ppid=parent),
             _proc(parent, "python.exe", [PY, r"C:\jarvis\scripts\run_backend_detached.py"])]
    row = _by_key(F.processes(table))["backend"]
    assert row.state == "ok"
    assert row.extra["count"] == 2, row.detail


def test_the_guardian_that_launched_the_backend_stays_visible():
    """Гардиан бэкенда — ДЕД панели по дереву процессов. Отсечение предков
    делало его невидимым, и панель докладывала «heartbeat свіжий, процесу не
    видно»: расхождение, которого в системе не было."""
    grandparent = psutil.Process(psutil.Process(os.getpid()).ppid()).ppid()
    table = [_proc(grandparent, "powershell.exe",
                   [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle",
                    "Hidden", "-File", r"C:\jarvis\scripts\backend_guardian_detached.ps1"])]
    row = _by_key(F.guardians(table))["backend_guardian"]
    assert "не видно" not in row.detail and "не видн" not in row.detail, row.detail
    assert str(grandparent) in row.detail or row.state == "ok", row.detail


@pytest.mark.parametrize("argv,key", [
    ([PY, "-u", "-m", "chatter.telethon_run", "--llm", "real"], "chatter"),
    ([PY, r"C:\jarvis\tools\jarvis_smart_telegram_control.py"], "bot"),
])
def test_module_and_script_launches_are_both_recognised(argv, key):
    """Раннеры запускаются по-разному: один модулем через -m, другой путём к
    файлу. Матчер обязан понимать обе формы — иначе «сузили безопасно»
    оборачивается молча невидимой службой."""
    row = _by_key(F.processes([_proc(4242, "python.exe", argv)]))[key]
    assert row.state == "ok", row.detail


# ─────────────── упоминание маркера — не запуск (ГРАБЛЯ 1) ───────────────────

def test_inline_python_code_that_mentions_a_marker_is_not_the_runner():
    """`python -c "...run_backend_detached..."` — это разговор О раннере, а не
    раннер. Прежняя защита ловила такой процесс, только если он был нашим
    предком; чужой диагностический скрипт красил бы ферму зелёным ни за что."""
    table = [_proc(4242, "python.exe",
                   [PY, "-c", "print('run_backend_detached is what we grep')"])]
    assert _by_key(F.processes(table))["backend"].state == "bad"


def test_a_powershell_one_liner_that_mentions_a_guardian_is_not_the_guardian():
    """`healthchecks_ping.ps1` и любой рестарт-однострочник несут имена скриптов
    ВНУТРИ `-Command`. Для гардианов проверки `py_only` нет вовсе, и до этой
    правки такой процесс засчитывался как живой сторож."""
    table = [_proc(4242, "powershell.exe",
                   [PS, "-NoProfile", "-Command",
                    "Get-Process | ? { $_.CommandLine -like '*backend_guardian_detached.ps1*' }"])]
    row = _by_key(F.guardians(table))["backend_guardian"]
    assert "процес є" not in row.detail, row.detail


def test_a_powershell_runner_is_not_a_python_runner():
    """`py_only` остаётся: маркеры раннеров ищутся только у python-процессов."""
    table = [_proc(4242, "powershell.exe",
                   [PS, "-File", r"C:\jarvis\scripts\run_backend_detached.py"])]
    assert _by_key(F.processes(table))["backend"].state == "bad"
