# -*- coding: utf-8 -*-
"""P16-а, ЛОВУШКА 1: «кто сторожит сторожа».

Спека `2026-07-25-ops-watchdog-chatter.md` §4.1 прямо предупреждает: добавить
chatter-пробы в `ops_watchdog`, у которого самого нет самоподъёма, — значит
перенести дыру 1 на уровень выше. Проверено фактом 29.07:

    JarvisChatterGuardian : AtStartup + AtLogOn + Time(PT10M)   ← (в) сделан
    JarvisOpsWatchdog     : AtStartup + AtLogOn                 ← дыра ОТКРЫТА

То есть независимый сторож, которому мы поручаем chatter, сам умирает до
перезагрузки — ровно как гардиан 20–22.07.

Контракт-тесты по исходнику `.ps1` (тот же приём, что у гардиана): держим то,
ЧТО будет зарегистрировано. Регистрация — шаг деплоя.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER = REPO_ROOT / "scripts" / "register_ops_watchdog.ps1"
WATCHDOG = REPO_ROOT / "scripts" / "ops_watchdog_detached.ps1"


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _code_lines(p: Path) -> list[str]:
    return [ln for ln in _text(p).splitlines() if not ln.lstrip().startswith("#")]


def test_watchdog_task_has_a_repetition_trigger():
    """Иначе сторож chatter'а сам лежит до ребута."""
    assert "RepetitionInterval" in _text(REGISTER), (
        "у JarvisOpsWatchdog нет повторяющегося триггера — мы поручили ему "
        "chatter, а он сам не поднимается (ловушка 1 спеки)")


def test_repetition_is_indefinite():
    m = re.search(r"RepetitionDuration\s+([^\s`]+)", _text(REGISTER))
    assert m is None, (
        f"задана RepetitionDuration ({m.group(1) if m else ''}) — конечная "
        f"длительность вернёт дыру молча")


def test_maxvalue_duration_is_never_used():
    """Регресс ровно того значения, на котором падал деплой (в): MaxValue →
    P99999999DT23H59M59S → Register-ScheduledTask падает 0x80041318, а таск
    молча остаётся со старым определением."""
    bad = [ln for ln in _code_lines(REGISTER) if "MaxValue" in ln and "TimeSpan]::Zero" not in ln]
    assert not bad, f"{bad} — Планировщик отвергнет такую длительность"


def test_registration_verifies_the_trigger_landed():
    """Код возврата не доказывает ничего: провал виден только в живом XML."""
    t = _text(REGISTER)
    assert "Get-ScheduledTask -TaskName $TaskName" in t, (
        "скрипт не перечитывает зарегистрированный таск")
    assert "Repetition.Interval" in t and "throw" in t, (
        "нет проверки-факта, что повторение принято Планировщиком")


def test_repetition_interval_is_sane():
    m = re.search(r"RepetitionInterval\s*\(New-TimeSpan\s+-Minutes\s+(\d+)\)", _text(REGISTER))
    assert m, "интервал повторения не читается"
    assert 5 <= int(m.group(1)) <= 30, f"интервал {m.group(1)} мин вне 5–30"


def test_boot_and_logon_triggers_are_kept():
    t = _text(REGISTER)
    assert "AtStartup" in t and "AtLogOn" in t, (
        "самоподъём ДОПОЛНЯЕТ, а не заменяет: после ребута сторож обязан встать "
        "сразу, не дожидаясь первого тика")


def test_single_instance_lock_is_present():
    """Повторяющийся триггер безопасен ТОЛЬКО потому, что второй экземпляр сам
    выходит по PID-локу. Без лока повторение начнёт плодить сторожей, и каждый
    будет слать свой алерт — владелец получит дубли на пустом месте."""
    w = _text(WATCHDOG)
    assert "ops_watchdog.pid" in w
    assert "another ops watchdog already running" in w


def test_multiple_instances_policy_is_ignore_new():
    assert re.search(r"MultipleInstances\s+IgnoreNew", _text(REGISTER)), (
        "без IgnoreNew повторяющийся триггер может наложить запуск на запуск")


def test_s4u_and_highest_are_kept():
    """Сторож обязан переживать логофф: без S4U он не увидит headless-ребут —
    ровно тот пробел, из-за которого backend умер в 03:14 незамеченным."""
    t = _text(REGISTER)
    assert "S4U" in t and "Highest" in t
