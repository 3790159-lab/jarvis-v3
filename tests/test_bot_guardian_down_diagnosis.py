"""Гардиан обязан сказать, БЫЛ ЛИ бот жив в момент, когда решил его снести.

17.08 бот перезапускался шесть раз за сутки, и каждый раз во время полного
прогона гейтов. Ротация boot-логов показала, что Python-исключения не было:
архив умершего экземпляра обрывается на обычных строках старта. Значит
объяснений ровно два — процесс умер сам либо перестал писать heartbeat живым, —
и различить их можно только ДО сноса: `Stop-OldBot` делает вопрос
неотвечаемым навсегда.

Сторожа проверяют три решения: что именно говорится про живость, что замер
несёт ЧИСЛА нагрузки (совпадение времени с гейтами — гипотеза, а не вывод), и
что замер снимается ДО подъёма и не может ему помешать.
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


def test_a_live_process_is_named_live_not_just_down(tmp_path):
    """Ловит: строку «bot DOWN», из которой не следует НИЧЕГО.

    Если процесс был жив, то умер не бот — умерла его отметка живости, и
    чинить надо порог или приоритет. Без этого слова разбор снова упрётся в
    догадку.
    """
    run = _run_ps(
        "Get-DownDiagnosis -Alive $true -HeartbeatAgeSec 412 -CpuPercent 97 "
        "-FreeRamMb 900 -PytestCount 3 -BotPid 11084", tmp_path)

    assert run.returncode == 0, run.stderr
    assert "процесс ЖИВ" in run.stdout, run.stdout
    assert "11084" in run.stdout, run.stdout


def test_a_dead_process_is_named_dead(tmp_path):
    """Парная: диагноз, который всегда говорит «жив», ничего не различает."""
    run = _run_ps(
        "Get-DownDiagnosis -Alive $false -HeartbeatAgeSec 999 -CpuPercent 12 "
        "-FreeRamMb 8000 -PytestCount 0", tmp_path)

    assert run.returncode == 0, run.stderr
    assert "процесс МЁРТВ" in run.stdout, run.stdout
    assert "ЖИВ" not in run.stdout.replace("МЁРТВ", ""), run.stdout


def test_the_diagnosis_carries_the_load_numbers(tmp_path):
    """Ловит: диагноз без ЗАМЕРА.

    Все шесть рестартов пришлись на прогон гейтов — совпадение сильное, но у
    нас уже был случай, когда очевидная причина оказалась третьей. Поэтому
    строка обязана нести числа, по которым гипотезу можно проверить или
    похоронить: возраст heartbeat, CPU, свободную память, число pytest.
    """
    run = _run_ps(
        "Get-DownDiagnosis -Alive $true -HeartbeatAgeSec 412 -CpuPercent 97 "
        "-FreeRamMb 900 -PytestCount 3 -BotPid 1", tmp_path)

    for needle in ("412", "97", "900", "pytest-прогонов 3"):
        assert needle in run.stdout, f"{needle!r} не попал в диагноз: {run.stdout}"


def test_a_real_measurement_answers_all_five_questions(tmp_path):
    """Ловит: замер, который на живой машине возвращает пустоту.

    Формат проверяется отдельно; здесь важно, что `Measure-DownContext`
    действительно ХОДИТ за числами и складывает их в ту же строку. Числа
    берутся с реальной машины, поэтому сверяются не значения, а то, что
    каждое поле заполнено и ни одно не осталось «-1» из-за опечатки в имени
    класса WMI.
    """
    run = _run_ps("Measure-DownContext", tmp_path)

    assert run.returncode == 0, run.stderr
    out = run.stdout
    assert "DIAGNOSIS:" in out, out
    assert "процесс МЁРТВ" in out, f"во временном -Root бота быть не может: {out}"
    assert "CPU -1" not in out and "RAM свободно -1" not in out, (
        f"замер нагрузки не отработал: {out}")
    assert "pytest-прогонов -1" not in out, f"счёт pytest не отработал: {out}"


def test_the_measurement_happens_before_the_kill_and_cannot_block_it():
    """Ловит: замер, снятый ПОСЛЕ сноса (то есть никакой), и замер, ставший
    условием подъёма.

    `Start-Bot` зовёт `Stop-OldBot`, и после него вопрос «был ли жив» уже не
    имеет ответа. А диагностика — удобство разбора; бот важнее, поэтому её
    падение обязано быть поймано.
    """
    src = SCRIPT.read_text(encoding="utf-8-sig")
    branch = src[src.index("bot DOWN - restarting"):]
    branch = branch[:branch.index("$started = Start-Bot")]

    assert "Measure-DownContext" in branch, (
        "диагноз снимается не в ветке DOWN — после Stop-OldBot он бессмыслен")
    assert "try" in branch and "catch" in branch, (
        "падение замера оборвало бы подъём бота")
