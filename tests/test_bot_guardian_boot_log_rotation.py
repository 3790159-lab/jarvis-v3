"""Последние слова умирающего бота обязаны пережить подъём следующего.

Класс ошибки, стоивший разбора 17.08: гардиан поднимает бота через
`Start-Process -RedirectStandardError`, а тот ОБРЕЗАЕТ файл при каждом
запуске. Бот перезапускался четырежды за сутки (01:08, 03:14, 03:59, 13:05),
каждый раз на третьей провалившейся проверке heartbeat и каждый раз во время
полного гейта — а отличить голодание под нагрузкой от настоящей смерти
процесса было нечем: трассировку падения затирал тот самый подъём, который её
и расследует.

Сторожа стоят на СЛЕДСТВИИ («прошлый вывод можно прочитать после подъёма»), а
не на способе его достичь: ротация, переименование, копия — дело реализации.

Границы, которые проверяются отдельно и намеренно:
  * пустой файл в архив не едет — иначе каталог зарастёт мусором и в нём
    потеряется тот единственный, где есть трассировка;
  * архив ограничен сверху — лог диагностики не имеет права съесть диск;
  * ОТКАЗ ротации не имеет права помешать подъёму бота: лог — удобство
    разбора, бот — прод.
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


def _run_ps(body: str, root: Path, timeout: int = 40) -> subprocess.CompletedProcess:
    """Тот же приём, что в test_bot_guardian_stop_old_bot: dot-source реального
    скрипта с временным -Root и -NoLoop, никакого C:\\jarvis."""
    command = f". '{SCRIPT}' -Root '{root}' -NoLoop\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def _logs(root: Path) -> Path:
    d = root / "state" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _archives(root: Path) -> list[Path]:
    return sorted(p for p in _logs(root).glob("bot_boot.stderr.*.log"))


def test_a_previous_crash_trace_survives_the_next_launch(tmp_path):
    """Ловит: разбор аварии, у которого нет улики.

    Именно этот сторож отвечает на вопрос «голодание или смерть»: после
    подъёма прошлый stderr обязан читаться целиком, а не начинаться заново.
    """
    logs = _logs(tmp_path)
    stderr = logs / "bot_boot.stderr.log"
    stderr.write_text("MemoryError: последние слова прошлого бота\n", encoding="utf-8")

    run = _run_ps("Rotate-BootLog (Join-Path $Root 'state\\logs\\bot_boot.stderr.log')",
                  tmp_path)
    assert run.returncode == 0, run.stderr

    saved = _archives(tmp_path)
    assert len(saved) == 1, f"улика не сохранена: {[p.name for p in _logs(tmp_path).iterdir()]}"
    assert "последние слова прошлого бота" in saved[0].read_text(encoding="utf-8")
    assert not stderr.exists(), (
        "путь остался занят — новый запуск допишет свой вывод в чужой файл, "
        "и два экземпляра станут неразличимы")


def test_an_empty_log_is_not_archived(tmp_path):
    """Ловит: архив, в котором не найти нужного.

    Бот, умерший молча, оставляет ПУСТОЙ stderr. Складывать такие файлы —
    значит хоронить единственный содержательный среди дюжины нулевых.
    """
    logs = _logs(tmp_path)
    (logs / "bot_boot.stderr.log").write_text("", encoding="utf-8")

    run = _run_ps("Rotate-BootLog (Join-Path $Root 'state\\logs\\bot_boot.stderr.log')",
                  tmp_path)
    assert run.returncode == 0, run.stderr
    assert _archives(tmp_path) == [], "пустой файл уехал в архив"


def test_the_archive_has_an_upper_bound(tmp_path):
    """Ловит: диагностику, которая съедает диск.

    Проба `disk` у сторожа считает свободное место; лог, который растёт без
    границы, превращает разбор аварии в её причину.
    """
    logs = _logs(tmp_path)
    body = []
    for i in range(5):
        body.append(
            "Set-Content -Path (Join-Path $Root 'state\\logs\\bot_boot.stderr.log') "
            f"-Value 'падение {i}' -Encoding utf8"
        )
        body.append(
            "Rotate-BootLog -Path (Join-Path $Root 'state\\logs\\bot_boot.stderr.log') -Keep 2"
        )
    run = _run_ps("\n".join(body), tmp_path)
    assert run.returncode == 0, run.stderr

    saved = _archives(tmp_path)
    assert len(saved) <= 2, f"граница не держится: {[p.name for p in saved]}"


def test_the_launch_path_actually_rotates_before_it_redirects():
    """Ловит: правильную функцию, которую никто не зовёт.

    Сторожа выше проверяют САМУ ротацию, и все они останутся зелёными, если
    вызов выпадет из `Start-Bot` — а тогда `-RedirectStandardError` обрежет
    файл ровно как раньше, и разбор снова упрётся в пустоту. Порядок здесь
    и есть поведение: сохранить надо ДО перенаправления, после уже нечего.

    Проверка текстовая сознательно: поднять `Start-Bot` целиком в тесте
    значит поднять настоящего бота, а этого в гейте делать нельзя.
    """
    src = SCRIPT.read_text(encoding="utf-8-sig")
    start = src.index("function Start-Bot")
    body = src[start:src.index("\n}", start)]

    assert "Rotate-BootLog" in body, "Start-Bot не зовёт ротацию — файл снова обрежется"
    assert body.index("Rotate-BootLog") < body.index("Start-Process"), (
        "ротация вызвана ПОСЛЕ Start-Process — сохранять уже нечего")
    assert body.count("Rotate-BootLog") >= 2, (
        "ротируется только один поток: stdout и stderr гасятся оба, и трассировка "
        "живёт как раз в stderr")


def test_a_rotation_that_cannot_happen_does_not_stop_the_bot(tmp_path):
    """Ловит: удобство разбора, ставшее условием подъёма прода.

    Файл может быть занят — антивирусом, открытым хвостом, чем угодно.
    Отказ ротации обязан быть ГРОМКИМ, но не фатальным: бот важнее лога.
    Проверяется и то, что улику при этом не потеряли.
    """
    logs = _logs(tmp_path)
    stderr = logs / "bot_boot.stderr.log"
    stderr.write_text("важная трассировка\n", encoding="utf-8")

    body = (
        "$p = Join-Path $Root 'state\\logs\\bot_boot.stderr.log'\n"
        "$h = [System.IO.File]::Open($p, 'Open', 'ReadWrite', 'None')\n"
        "try { Rotate-BootLog $p; 'ROTATE-RETURNED' } finally { $h.Close() }\n"
    )
    run = _run_ps(body, tmp_path)

    assert run.returncode == 0, run.stderr
    assert "ROTATE-RETURNED" in run.stdout, (
        f"ротация бросила исключение и оборвала бы подъём бота: {run.stdout}\n{run.stderr}")
    assert stderr.read_text(encoding="utf-8").strip() == "важная трассировка", (
        "занятый файл потерян — ротация уничтожила то, что обязана была сохранить")
