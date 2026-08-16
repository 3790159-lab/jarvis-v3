# -*- coding: utf-8 -*-
"""Сторожа на ОСНОВНОЙ путь гардиана: увидел мёртвого — поднял нового.

Почему отдельным файлом и почему вообще: инцидент 16.08. Гардиан 13 ч 42 мин
держал volska в состоянии DOWN и не сделал НИ ОДНОЙ попытки подъёма, а
`tests/test_chatter_guardian_multiclient.py` был весь зелёный. Причина —
покрытие обходило ровно ту ветку, ради которой гардиан существует:

    PLAN_DISABLED  -> ветка «выключен, остановить»
    PLAN_CONFLICT  -> ветка «invalid, не трогать»
    PLAN_MIXED     -> только Write-ClientState, без Invoke-Converge
    fatal-реестр   -> ранний выход

Ветка `runnable && -not Test-Runner` -> Start-Runner не проверялась ничем.

Сторожа ниже написаны ОТ КОНТРАКТА, а не от найденной причины: они обязаны
краснеть на ЛЮБУЮ причину, по которой тело Start-Runner не исполнилось —
опечатка в имени параметра, сломанный Start-Process, ранний return, — а не
только на конкретную грабли `-Db` против алиаса `-Debug`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="гардиан Windows-only (PowerShell + taskkill + Win32_Process)"),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "chatter_guardian_detached.ps1"

PLAN_DOWN = """
{"fatal": null, "clients": [
  {"slug":"aaa","desired":"enabled","runnable":true,"error":null,
   "personas":["aaa"],"session":".secrets/aaa.session","db":".secrets/aaa.db"}]}
"""


def _run_ps(body: str, root: Path, timeout: int = 90) -> subprocess.CompletedProcess:
    command = f". '{SCRIPT}' -Root '{root}' -NoLoop\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")


def _converge_until_down(root: Path, plan_json: str) -> subprocess.CompletedProcess:
    """Три прохода конвергенции: дебаунс DebounceFailures=3 -> ветка DOWN.

    $py подменяем на заглушку: тело Start-Runner обязано ИСПОЛНИТЬСЯ, но
    поднимать настоящий chatter.telethon_run на стенде нельзя — он полез бы в
    Telegram и в чужую .session.
    """
    stub = root / "stub.cmd"
    stub.write_text("@echo off\r\necho [stub] %*\r\nexit /b 0\r\n", encoding="ascii")
    body = (
        f"$py = '{stub}'\n"
        f"$plan = @'\n{plan_json}\n'@ | ConvertFrom-Json\n"
        "1..3 | ForEach-Object { Invoke-Converge -Plan $plan | Out-Null }\n"
    )
    return _run_ps(body, root)


def _guardian_log(root: Path) -> str:
    p = root / "logs" / "chatter_guardian.stdout.log"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


# ── контракт 1: мёртвый клиент ОБЯЗАН привести к попытке запуска ───────────

def test_down_client_leads_to_an_actual_launch_attempt(tmp_path):
    """То, ради чего гардиан существует. Если тело Start-Runner не исполнилось,
    клиент лежит молча — ровно инцидент 16.08 (13 ч 42 мин без единой попытки).

    Проверяем СЛЕД ИСПОЛНЕНИЯ в логе, а не возврат функции: возврат можно
    получить и не войдя в тело."""
    res = _converge_until_down(tmp_path, PLAN_DOWN)
    log = _guardian_log(tmp_path)

    assert "runner DOWN" in log, f"не дошли до ветки DOWN за 3 прохода:\n{log}"
    assert "launched chatter runner" in log, (
        "гардиан объявил клиента DOWN и НЕ попытался его поднять — "
        f"тело Start-Runner не исполнилось.\nЛОГ:\n{log}\nSTDERR:\n{res.stderr}")


# ── контракт 2: тишина в stderr; returncode==0 оказался слепым ─────────────

def test_converge_does_not_emit_powershell_errors(tmp_path):
    """16.08 ошибка привязки параметров была NON-TERMINATING: цикл шёл дальше,
    гардиан бился heartbeat'ом, `returncode` оставался 0 — и все прежние
    assert'ы на returncode проходили поверх сломанного подъёма.

    Значит returncode не сторож. Сторож — пустой stderr."""
    res = _converge_until_down(tmp_path, PLAN_DOWN)
    noise = [ln for ln in res.stderr.splitlines() if ln.strip()]
    assert not noise, (
        "PowerShell ругался в stderr во время конвергенции — при "
        "$ErrorActionPreference='Continue' это молча ломает шаг и не роняет "
        "цикл:\n" + "\n".join(noise[:20]))


# ── контракт 3: структурный — имена параметров не должны быть отравлены ────

def _reserved_aliases() -> set[str]:
    """Спрашиваем у САМОГО PowerShell, а не держим список руками: набор общих
    параметров зависит от версии, а захардкоженный список протухнет молча."""
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command",
         "function __probe { [CmdletBinding()] param() };"
         "(Get-Command __probe).Parameters.Values | "
         "ForEach-Object { $_.Aliases } | Sort-Object -Unique"],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert res.returncode == 0, res.stderr
    return {ln.strip().lower() for ln in res.stdout.splitlines() if ln.strip()}


def _advanced_function_params(text: str) -> dict[str, list[str]]:
    """{имя функции: [имена параметров]} для функций, чей param-блок содержит
    [Parameter(...)] — только такие становятся advanced и получают общие
    параметры вместе с их алиасами."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"function\s+([A-Za-z][\w-]*)\s*\{", text):
        name = m.group(1)
        chunk = text[m.end():m.end() + 1500]
        pm = re.search(r"param\s*\((.*?)\n\s*\)", chunk, re.S)
        if not pm:
            continue
        block = pm.group(1)
        if "[Parameter(" not in block:
            continue
        out[name] = re.findall(r"\$([A-Za-z]\w*)", block)
    return out


def test_no_parameter_name_collides_with_a_common_parameter_alias():
    """Класс граблей, а не один случай.

    Функция с [Parameter(...)] становится ADVANCED и молча получает общие
    параметры со своими алиасами. Параметр `-Db` совпал с алиасом `-Debug`,
    и КАЖДЫЙ вызов падал на привязке — до первой строки тела, поэтому в логе
    не было ни одной строки Start-Runner, хотя все они безусловные."""
    text = SCRIPT.read_text(encoding="utf-8", errors="replace")
    reserved = _reserved_aliases()
    bad = []
    for func, params in _advanced_function_params(text).items():
        for p in params:
            if p.lower() in reserved:
                bad.append(f"{func}: -{p} совпадает с алиасом общего параметра")
    assert not bad, (
        "имя параметра отравлено алиасом общего параметра — вызов упадёт на "
        "ПРИВЯЗКЕ, тело не исполнится:\n" + "\n".join(bad))
