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
    """Бессрочность в схеме Планировщика = `<Repetition>` БЕЗ `<Duration>`.

    Приёмка 25.07: первая редакция задавала `-RepetitionDuration
    ([TimeSpan]::MaxValue)`, что сериализуется в `P99999999DT23H59M59S` и
    отвергается валидатором XML (`HRESULT 0x80041318`, "value which is
    incorrectly formatted or out of range"). Register-ScheduledTask падал,
    таск оставался со СТАРЫМИ двумя триггерами — а этот тест был зелёным,
    потому что читал текст скрипта, а не то, что принял Windows.
    """
    t = _text(REGISTER)
    m = re.search(r"RepetitionDuration\s+([^\s`]+)", t)
    assert m is None, (
        f"задана RepetitionDuration ({m.group(1) if m else ''}) — конечная "
        f"длительность вернёт дыру молча, а [TimeSpan]::MaxValue Планировщик "
        f"вообще отвергнет (0x80041318). Бессрочно = НЕ задавать Duration.")


def _code_lines(p: Path) -> list[str]:
    """Только исполняемые строки: в комментариях `MaxValue` живёт законно —
    там объяснено, ПОЧЕМУ его нельзя передавать."""
    return [ln for ln in _text(p).splitlines() if not ln.lstrip().startswith("#")]


def test_maxvalue_duration_is_never_used():
    """Сторож против регресса ровно того значения, на котором падал деплой."""
    bad = [ln for ln in _code_lines(REGISTER) if "MaxValue" in ln]
    assert not bad, (
        f"{bad} — [TimeSpan]::MaxValue → P99999999DT23H59M59S → "
        f"Register-ScheduledTask падает с 0x80041318 и таск остаётся без "
        f"самоподъёма")


def test_registration_verifies_the_trigger_landed():
    """Код возврата не доказывает ничего: провал регистрации виден только в
    живом XML. Скрипт обязан САМ перечитать таск и упасть, если повторения
    нет — иначе следующий деплой снова оставит дыру молча."""
    t = _text(REGISTER)
    assert "Get-ScheduledTask -TaskName $TaskName" in t, (
        "скрипт не перечитывает зарегистрированный таск")
    assert "Repetition.Interval" in t and "throw" in t, (
        "нет проверки-факта, что повторяющийся триггер принят Планировщиком")


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
