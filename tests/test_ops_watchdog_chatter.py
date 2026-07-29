"""P16-а: `ops_watchdog` += chatter — независимый от гардиана канал.

Спека `docs/superpowers/specs/2026-07-25-ops-watchdog-chatter.md`.

Дыра, которую закрываем: сегодня о смерти chatter сообщает только
`chatter_watch_check.py`, а его зовёт ЕДИНСТВЕННОЕ место — сам гардиан-скрипт.
Умер гардиан — умер и алертер, и тишина неотличима от здоровья. 20–22.07 раннер
пролежал ~44 часа, и не сработал ни один сторож.

Пробы — ЧИСТЫЕ функции над снимком (§3), поэтому дебаунс/алерты/восстановление
переиспользуют существующий `evaluate()` без правок.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_c",
    Path(__file__).resolve().parents[1] / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ow)

ROOT = "C:/jarvis"
RUNNER_CMD = r"C:\jarvis\.venv\Scripts\python.exe -u -m chatter.telethon_run --llm real"
GUARD_CMD = (r"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
             r"-File C:\jarvis\scripts\chatter_guardian_detached.ps1")


def _proc(pid, name, cmdline):
    return {"pid": pid, "name": name, "cmdline": cmdline}


# ─────────────────────────────────────────────── проба раннера

def test_runner_alive_with_fresh_beat_is_ok():
    p = ow.probe_chatter_runner([_proc(111, "python.exe", RUNNER_CMD)],
                                beat_age=12.0, root=ROOT)
    assert p["ok"] is True


def test_runner_missing_process_is_down():
    p = ow.probe_chatter_runner([_proc(1, "explorer.exe", "explorer")],
                                beat_age=12.0, root=ROOT)
    assert p["ok"] is False
    assert "процес" in p["detail"].lower() or "process" in p["detail"].lower()


def test_runner_stale_beat_is_down_even_when_process_alive():
    """Живой процесс с протухшим beat — это зависший раннер, а не здоровье."""
    p = ow.probe_chatter_runner([_proc(111, "python.exe", RUNNER_CMD)],
                                beat_age=999.0, root=ROOT)
    assert p["ok"] is False
    assert "heartbeat" in p["detail"].lower()


def test_runner_missing_beat_file_is_down():
    p = ow.probe_chatter_runner([_proc(111, "python.exe", RUNNER_CMD)],
                                beat_age=None, root=ROOT)
    assert p["ok"] is False


def test_runner_probe_does_not_match_a_non_python_process():
    """ЛОВУШКА самоматча (стоила ложного «раннер поднялся» 29.07): строка
    `chatter.telethon_run` попадает в командную строку того, кто ИЩЕТ раннера
    (powershell/grep), и проверка находит сама себя."""
    searcher = _proc(222, "powershell.exe",
                     "powershell -Command Get-CimInstance ... 'chatter.telethon_run'")
    p = ow.probe_chatter_runner([searcher], beat_age=5.0, root=ROOT)
    assert p["ok"] is False, "проба поймала процесс-искатель вместо раннера"


def test_runner_probe_requires_our_root_in_cmdline():
    """Чужой chatter из другого дерева (worktree разработчика) не считается
    боевым раннером — иначе сторож замолчит, пока рядом крутится тест."""
    other = _proc(333, "python.exe",
                  r"C:\jarvis_worktrees\panels\.venv\Scripts\python.exe -m chatter.telethon_run")
    p = ow.probe_chatter_runner([other], beat_age=5.0, root=ROOT)
    assert p["ok"] is False


# ─────────────────────────────────────────────── проба гардиана

def test_guardian_alive_with_fresh_beat_is_ok():
    p = ow.probe_chatter_guardian([_proc(5964, "powershell.exe", GUARD_CMD)],
                                  lock_pid=5964, beat_age=10.0)
    assert p["ok"] is True


def test_guardian_missing_lock_is_down():
    p = ow.probe_chatter_guardian([_proc(5964, "powershell.exe", GUARD_CMD)],
                                  lock_pid=None, beat_age=10.0)
    assert p["ok"] is False


def test_guardian_dead_pid_is_down():
    p = ow.probe_chatter_guardian([_proc(1, "explorer.exe", "explorer")],
                                  lock_pid=5964, beat_age=10.0)
    assert p["ok"] is False


def test_stale_lock_pointing_at_a_reused_pid_is_down():
    """ЛОВУШКА 3 спеки: PID-файл переживает kill. Если ОС успела выдать тот же
    номер чужому процессу, «PID существует» — это не «гардиан жив»."""
    impostor = _proc(5964, "chrome.exe", "chrome.exe --type=renderer")
    p = ow.probe_chatter_guardian([impostor], lock_pid=5964, beat_age=10.0)
    assert p["ok"] is False
    assert "powershell" in p["detail"].lower()


def test_guardian_stale_beat_is_down():
    p = ow.probe_chatter_guardian([_proc(5964, "powershell.exe", GUARD_CMD)],
                                  lock_pid=5964, beat_age=999.0)
    assert p["ok"] is False


# ─────────────────────────────────────────────── пороги и ловушки

def test_thresholds_match_the_guardian_so_two_watchdogs_never_disagree():
    """Инцидент 13:06: `chatter_watch_check` выводил вердикт по СВОЕМУ порогу и
    противоречил гардиану. Порог обязан быть один — 180 с."""
    assert ow.CHATTER_BEAT_MAX_AGE_S == 180

    ok = ow.probe_chatter_runner([_proc(1, "python.exe", RUNNER_CMD)],
                                 beat_age=179.0, root=ROOT)
    bad = ow.probe_chatter_runner([_proc(1, "python.exe", RUNNER_CMD)],
                                  beat_age=181.0, root=ROOT)
    assert ok["ok"] is True and bad["ok"] is False


@pytest.mark.parametrize("flag_present", [True, False])
def test_semidemo_flag_does_not_change_the_verdict(flag_present):
    """ЛОВУШКА 4: проба меряет ПРОЦЕСС. Отключённые флагом клиенты — это (б)/(г);
    алертить по ним здесь нельзя, иначе сторож вечно кричит на законное
    состояние."""
    procs = [_proc(111, "python.exe", RUNNER_CMD)]
    p = ow.probe_chatter_runner(procs, beat_age=10.0, root=ROOT,
                                semidemo_flag=flag_present)
    assert p["ok"] is True


def test_alert_text_names_the_independent_source():
    """ЛОВУШКА 2: о смерти раннера при живом гардиане скажут ОБА канала. Тексты
    обязаны различаться источником, иначе владелец решит, что упало дважды."""
    txt = ow.build_alert("chatter_runner", "down", "процес не знайдено")
    assert "CHATTER" in txt.upper()
    assert "сторож" in txt.lower(), f"источник не назван: {txt}"


# ─────────────────────────────────────────────── интеграция в evaluate()

def test_chatter_probes_go_through_the_existing_debounce():
    prev = {}
    down = {"chatter_runner": {"ok": False, "detail": "процес не знайдено"}}

    alerts, st = ow.evaluate(prev, down)
    assert alerts == [], "алерт на первом же цикле — дебаунс не применился"

    alerts, st = ow.evaluate(st, down)
    assert len(alerts) == 1 and "DOWN" in alerts[0]

    alerts, st = ow.evaluate(st, {"chatter_runner": {"ok": True, "detail": "ok"}})
    assert len(alerts) == 1 and "Восстановлено" in alerts[0]


def test_probe_all_includes_chatter_when_snapshot_supplied():
    """Пробы подключены к боевому составу цикла, а не живут отдельно."""
    probes = ow.probe_all(
        lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30),
        chatter_snapshot={
            "processes": [_proc(111, "python.exe", RUNNER_CMD),
                          _proc(5964, "powershell.exe", GUARD_CMD)],
            "runner_beat_age": 5.0,
            "guardian_beat_age": 5.0,
            "guardian_lock_pid": 5964,
            "root": ROOT,
        })
    assert probes["chatter_runner"]["ok"] is True
    assert probes["chatter_guardian"]["ok"] is True


def test_probe_all_without_snapshot_keeps_old_behaviour():
    """Обратная совместимость: без снимка состав проб прежний (иначе живой
    watchdog на старом окружении начнёт слать DOWN о том, чего не мерил)."""
    probes = ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30))
    assert "chatter_runner" not in probes
    assert "chatter_guardian" not in probes
