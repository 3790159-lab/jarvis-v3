# -*- coding: utf-8 -*-
"""Код выхода бота: то единственное, что различает убийство и самовыход.

Отпечаток (`bot_death.log`) 17.08 дал вердикт **killed**: ни трассировки
faulthandler, ни метки `EXIT-CLEAN`. Но он по построению НЕ различает два
случая — внешний `TerminateProcess` и `os._exit()`: оба обрывают процесс,
минуя atexit. Код выхода различает их одним числом:

    1          taskkill /F  (TerminateProcess)
    0          вышел сам (в боте это ровно два os._exit(0))
    0xC000xxxx крах процесса

Спросить может ТОЛЬКО запустивший: хэндл процесса есть у гардиана и больше
ни у кого. Поэтому расшифровка живёт в гардиане, а сторожа — здесь.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="guardian script is Windows-only (PowerShell + taskkill + Win32_Process)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "bot_guardian_detached.ps1"


def _run_ps(body: str, root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    command = f". '{SCRIPT}' -Root '{root}' -NoLoop\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")


def test_code_one_reads_as_an_external_kill(tmp_path):
    """Ловит: диагноз, который не отвечает на главный вопрос недели.

    taskkill /F завершает процесс с кодом 1. Если этот код не назван вслух,
    разбор снова упрётся в «умер сам» без имени убийцы.
    """
    run = _run_ps("Get-ExitCodeVerdict 1", tmp_path)
    assert run.returncode == 0, run.stderr
    assert "УБИТ снаружи" in run.stdout, run.stdout


def test_code_zero_reads_as_a_self_exit(tmp_path):
    """Парная: 0 — это НЕ убийство, а два известных os._exit(0) в боте.

    Спутать эти два случая значит неделю искать внешнего убийцу там, где бот
    уходит сам по своей же команде.
    """
    run = _run_ps("Get-ExitCodeVerdict 0", tmp_path)
    assert "вышел САМ" in run.stdout, run.stdout
    assert "УБИТ" not in run.stdout, run.stdout


@pytest.mark.parametrize("code,label", [
    (0xC0000409, "КРАХ"),   # fail-fast CRT — ровно то, чем падает WindowsTerminal
    (0xC0000005, "КРАХ"),   # access violation
])
def test_ntstatus_codes_read_as_a_crash(tmp_path, code, label):
    """Крах обязан отличаться от убийства: у него другой источник и другая
    починка. `faulthandler` ловит не всякий крах, поэтому код выхода здесь —
    второй независимый признак."""
    run = _run_ps(f"Get-ExitCodeVerdict {code}", tmp_path)
    assert label in run.stdout, run.stdout


def test_an_unavailable_code_says_so_instead_of_guessing(tmp_path):
    """Ловит: молчаливый дефолт. Нет хэндла — нет ответа, и это ОТДЕЛЬНОЕ
    показание, а не «вышел сам»."""
    run = _run_ps("Get-ExitCodeVerdict $null", tmp_path)
    assert "недоступен" in run.stdout, run.stdout
    assert "вышел САМ" not in run.stdout, run.stdout


def test_the_diagnosis_line_carries_the_exit_verdict(tmp_path):
    """Ловит: расшифровку, которая никуда не попадает.

    Вердикт обязан ехать В ТОЙ ЖЕ строке журнала, что и остальной замер:
    разбирающий читает одну строку, а не собирает картину из трёх мест.
    """
    run = _run_ps(
        "Get-DownDiagnosis -Alive $false -HeartbeatAgeSec 91 -CpuPercent 15 "
        "-FreeRamMb 8851 -PytestCount 2 -ExitVerdict 'код 1 - УБИТ снаружи'", tmp_path)
    assert "процесс МЁРТВ" in run.stdout and "УБИТ снаружи" in run.stdout, run.stdout


def test_a_live_process_is_never_asked_for_an_exit_code():
    """Ловит: вопрос, заданный не тому.

    У живого процесса кода выхода НЕТ, и обращение к `.ExitCode` бросает
    исключение — прямо в ветке, которая обязана довести бота до подъёма.
    Проверка текстовая: поднять живого бота в гейте нельзя.
    """
    src = SCRIPT.read_text(encoding="utf-8-sig")
    block = src[src.index("$exitVerdict = ''"):src.index("return (Get-DownDiagnosis")]

    assert "-not $alive" in block, "код выхода спрашивают, не проверив смерть"
    assert "HasExited" in block, "нет проверки, что процесс действительно завершился"
    assert "try" in block and "catch" in block, "исключение оборвало бы подъём бота"
