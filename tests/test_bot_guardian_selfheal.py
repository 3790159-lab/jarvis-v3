# -*- coding: utf-8 -*-
"""Самоподъём гардиана ГЛАВНОГО БОТА — та же ловушка, что у ops_watchdog.

Проверено фактом 2026-08-05 по живому Планировщику:

    JarvisBackendGuardian : AtStartup + AtLogOn + Time(PT5M)    ← прикрыт
    JarvisChatterGuardian : AtStartup + AtLogOn + Time(PT10M)   ← прикрыт
    JarvisOpsWatchdog     : AtStartup + AtLogOn + Time(PT10M)   ← прикрыт 29.07
    JarvisBotGuardian     : AtStartup + AtLogOn                 ← ДЫРА

Цена дыры в тот же день, числами: главный бот стартовал 01.08 05:13, а его
гардиан последний раз запускался 31.07 15:02 — то есть бот работал БЕЗ
присмотра. Умри гардиан — никто не поднимет бота до перезагрузки или
интерактивного логона, а S4U-таск как раз и заводили ради headless-режима.

Интервал выбран PT5M (как у backend, не как у chatter): главный бот —
основной канал управления владельца, худшее окно простоя вдвое короче.
Стоимость повтора нулевая: при живом гардиане запуск гасится
MultipleInstances=IgnoreNew.

Контракт-тесты по исходнику `.ps1` — тот же приём, что у ops_watchdog:
держим то, ЧТО будет зарегистрировано. Сама регистрация — шаг деплоя, её
делает владелец руками.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER = REPO_ROOT / "scripts" / "register_bot_guardian.ps1"


def _text() -> str:
    return REGISTER.read_text(encoding="utf-8", errors="replace")


def _code_lines() -> list[str]:
    return [ln for ln in _text().splitlines() if not ln.lstrip().startswith("#")]


def test_bot_guardian_task_has_a_repetition_trigger():
    """Без повторяющегося триггера мёртвый гардиан лежит до ребута."""
    assert "RepetitionInterval" in _text(), (
        "у JarvisBotGuardian нет повторяющегося триггера — главный бот "
        "остаётся без присмотра до перезагрузки или логона")


def test_repetition_interval_is_five_minutes():
    """Пин выбранного окна: backend-уровень, не chatter-уровень."""
    interval = re.search(r"RepetitionInterval\s*\(New-TimeSpan\s+-Minutes\s+(\d+)\)", _text())
    assert interval is not None, "интервал повтора не задан явным New-TimeSpan"
    assert interval.group(1) == "5", (
        f"интервал {interval.group(1)} мин — для главного бота держим 5")


def test_repetition_is_indefinite():
    """Конечная длительность вернёт дыру молча, когда окно истечёт."""
    duration = re.search(r"RepetitionDuration\s+([^\s`]+)", _text())
    assert duration is None, (
        f"задана RepetitionDuration ({duration.group(1) if duration else ''}) — "
        f"самоподъём умрёт по истечении окна")


def test_maxvalue_duration_is_never_used():
    """Регресс значения, на котором падала регистрация ops_watchdog:
    MaxValue -> P99999999DT23H59M59S -> Register-ScheduledTask 0x80041318, а
    таск молча остаётся со старым определением."""
    bad = [ln for ln in _code_lines() if "MaxValue" in ln and "TimeSpan]::Zero" not in ln]
    assert not bad, f"{bad} — Планировщик отвергнет такую длительность"


def test_registration_verifies_the_trigger_landed():
    """Код возврата не доказывает ничего: деплой 25.07 молча оставил дыру
    у ops_watchdog. Провал виден только в перечитанном живом XML."""
    text = _text()
    assert "Get-ScheduledTask -TaskName $TaskName" in text, (
        "скрипт не перечитывает живой таск после регистрации")
    assert "throw" in text, (
        "скрипт не падает, когда Планировщик не принял триггер")


def test_startup_and_logon_triggers_are_kept():
    """Повтор ДОПОЛНЯЕТ, а не заменяет: headless-ребут поднимает бота
    именно AtStartup."""
    text = _text()
    assert "-AtStartup" in text and "-AtLogOn" in text, (
        "потеряны исходные триггеры — regression по headless-восстановлению")


def test_script_stays_ascii_only():
    """Файл исторически ASCII без BOM. Не-ASCII без BOM ломает PS 5.1
    (tests/test_ps1_encoding_contract.py); не втаскиваем эту зависимость."""
    raw = REGISTER.read_bytes()
    assert all(byte < 128 for byte in raw), (
        "в скрипте появились не-ASCII байты — тогда обязателен UTF-8 BOM")


def test_task_stays_session_independent():
    """S4U + Highest — то, ради чего таск и заводили (headless-ребут)."""
    text = _text()
    assert "S4U" in text and "Highest" in text
    assert "IgnoreNew" in text, (
        "без IgnoreNew повторный триггер плодил бы вторые копии гардиана")
