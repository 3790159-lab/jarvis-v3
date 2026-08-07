# -*- coding: utf-8 -*-
"""Контракт регистрации JarvisStateBackup — по образцу
test_bot_guardian_selfheal.py / test_ops_watchdog_selfheal.py.

Держим то, ЧТО будет зарегистрировано; саму регистрацию делает владелец
руками отдельным шагом деплоя (и только после зелёной живой заливки).

Прецедент, ради которого здесь проверка фактом: деплой 25.07 у гардиана
молча оставил дыру — `Register-ScheduledTask` вернул успех, а Планировщик
определение не принял. Код возврата не доказывает ничего; провал виден
только в перечитанном живом XML.

Отдельная причина именно для этого таска (разведка Б2, 2026-08-06): таск
не был зарегистрирован НИКОГДА, облачного бэкапа не существовало ни одного
дня. Первая же регистрация обязана доказать себя, а не пополнить список
«зелёных» тасков без результата.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER = REPO_ROOT / "scripts" / "register_state_backup.ps1"


def _text() -> str:
    return REGISTER.read_text(encoding="utf-8-sig", errors="replace")


def _code_lines() -> list[str]:
    return [ln for ln in _text().splitlines() if not ln.lstrip().startswith("#")]


def test_registration_verifies_the_task_landed():
    """Главный контракт: перечитать живой таск и упасть, если не принят."""
    text = _text()
    assert "Get-ScheduledTask -TaskName $TaskName" in text, (
        "скрипт не перечитывает живой таск после регистрации — провал "
        "регистрации останется молчаливым")
    assert "throw" in text, (
        "скрипт не падает, когда Планировщик не принял определение")


def test_registration_verifies_the_daily_trigger_specifically():
    """Мало убедиться, что таск существует: без триггера он не побежит
    никогда, а выглядеть будет как зарегистрированный."""
    text = _text()
    assert re.search(r"\$reg\.Triggers", text), (
        "живые триггеры не перечитываются — «таск есть» не значит «побежит»")


def test_registration_verifies_principal_is_session_independent():
    """S4U/Highest — то, ради чего таск и заводится (headless, без логона).
    Планировщик может принять таск и отвергнуть принципала."""
    text = _text()
    assert re.search(r"\$reg\.Principal", text), (
        "принципал не перечитывается — таск мог зарегистрироваться "
        "интерактивным и не запуститься без логона")


def test_task_stays_session_independent():
    text = _text()
    assert "S4U" in text and "Highest" in text
    assert "IgnoreNew" in text, "без IgnoreNew возможны параллельные заливки"


def test_daily_trigger_is_declared():
    text = _text()
    assert re.search(r"New-ScheduledTaskTrigger\s+-Daily", text), (
        "суточный триггер потерян — DEV-16 задумывал именно суточный бэкап")


def test_action_runs_the_backup_script_with_repo_python():
    text = _text()
    assert "scripts\\state_backup.py" in text or "scripts/state_backup.py" in text
    assert ".venv\\Scripts\\python.exe" in text, (
        "системный python не увидит зависимостей (boto3) — таск упадёт")


def test_start_when_available_is_kept():
    """Машина выключена в 03:30 -> прогон должен догнаться, а не пропасть."""
    assert "StartWhenAvailable" in _text()


def test_registration_does_not_run_the_backup():
    """Регистрация не должна сама бить по R2: живой прогон — отдельный,
    осознанный шаг (граница владельца в Б2)."""
    bad = [ln for ln in _code_lines()
           if re.search(r"schtasks\s+/Run|Start-ScheduledTask", ln)]
    assert not bad, f"регистрация запускает прогон сама: {bad}"


def test_script_encoding_contract():
    """Не-ASCII в .ps1 обязан идти с UTF-8 BOM, иначе PS 5.1 читает файл
    как windows-1251 и падает парсером (tests/test_ps1_encoding_contract.py).
    Здесь дублируем адресно: правки ассистента срезают BOM молча."""
    raw = REGISTER.read_bytes()
    if any(b > 127 for b in raw):
        assert raw.startswith(b"\xef\xbb\xbf"), (
            "в скрипте есть не-ASCII байты, но UTF-8 BOM срезан — "
            "PowerShell 5.1 не распарсит файл")
