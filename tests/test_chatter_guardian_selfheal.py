# -*- coding: utf-8 -*-
"""P16(в): гардиан chatter обязан подниматься сам, без ребута.

Инцидент 2026-07-20…22 (форензика 25.07): PowerShell-процесс гардиана умер в
середине сессии, раннер умер следом — и НИКТО его не поднял почти двое суток.
Триггеры таска были только `AtStartup` + `AtLogon`, то есть единственный путь
восстановления — перезагрузка или новый логон.

Контракт-тесты по исходнику `.ps1` (тот же приём, что `test_add_secret_ps1.py`):
проверяем декларацию таска, а не поведение Windows. Регистрация таска — шаг
деплоя, здесь мы держим то, ЧТО будет зарегистрировано.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER = REPO_ROOT / "scripts" / "register_chatter_guardian.ps1"
GUARDIAN = REPO_ROOT / "scripts" / "chatter_guardian_detached.ps1"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def test_register_script_exists():
    assert REGISTER.is_file(), "нечего регистрировать — таск гардиана без скрипта"


def test_task_has_a_repetition_trigger():
    """Главное: смерть процесса лечится сама, а не ребутом."""
    t = _text(REGISTER)
    assert "RepetitionInterval" in t, (
        "у таска нет повторяющегося триггера — умерший гардиан пролежит "
        "до перезагрузки (ровно инцидент 20-22.07)")


def test_repetition_is_indefinite_not_a_one_off():
    """`RepetitionDuration` без бесконечности = самоподъём протухнет через N
    часов, и дыра вернётся молча."""
    t = _text(REGISTER)
    m = re.search(r"RepetitionDuration\s+([^\s`]+)", t)
    assert m, "не задана длительность повторения"
    assert "Max" in m.group(1) or "MaxValue" in m.group(1), (
        f"повторение конечно ({m.group(1)}) — после его истечения гардиан снова "
        f"некому поднять")


def test_repetition_interval_is_sane():
    """Слишком часто — лишние процессы и лог-шум; слишком редко — окно простоя.
    Ждём 5–30 минут."""
    t = _text(REGISTER)
    m = re.search(r"RepetitionInterval\s*\(New-TimeSpan\s+-Minutes\s+(\d+)\)", t)
    assert m, "интервал повторения не читается"
    assert 5 <= int(m.group(1)) <= 30, f"интервал {m.group(1)} мин вне 5–30"


def test_boot_and_logon_triggers_are_kept():
    """Самоподъём ДОПОЛНЯЕТ, а не заменяет: после ребута гардиан обязан встать
    сразу, не дожидаясь первого тика повторения."""
    t = _text(REGISTER)
    assert "AtStartup" in t and "AtLogOn" in t


def test_guardian_single_instance_lock_is_present():
    """Повторяющийся триггер безопасен ТОЛЬКО потому, что второй экземпляр сам
    выходит по PID-локу. Если лок исчезнет, повторение начнёт плодить
    гардианов, которые будут наперегонки убивать раннер друг друга."""
    g = _text(GUARDIAN)
    assert "chatter_guardian.pid" in g
    assert "another chatter guardian already running" in g


def test_multiple_instances_policy_is_ignore_new():
    """Планировщик не должен запускать второй экземпляр поверх живого — это
    вторая линия защиты к PID-локу."""
    t = _text(REGISTER)
    assert re.search(r"MultipleInstances\s+IgnoreNew", t), (
        "без IgnoreNew повторяющийся триггер может наложить запуск на запуск")
