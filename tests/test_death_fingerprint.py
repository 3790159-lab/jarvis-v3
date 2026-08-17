# -*- coding: utf-8 -*-
"""Три версии смерти — три РАЗНЫХ следа. Сторожа проверяют каждый живьём.

Повод: 17.08 бот умер 16 раз, и после каждой смерти вопрос «от чего» не имел
ответа. Гардианский `DIAGNOSIS` закрыл голодание и OOM (процесс МЁРТВ при 12%
CPU и 8.7 ГБ свободно), архивы `bot_boot.stderr.*` закрыли Python-исключение —
трассировки нет ни в одном. Остались три версии, и каждая обязана оставлять
свой отпечаток, иначе следующая смерть снова ничего не скажет.

Проверяется НАСТОЯЩИМИ процессами, а не моками: крах вызывается
`faulthandler._sigsegv()`, убийство — `taskkill /F`. Мок здесь доказал бы, что
код написан, а нужно доказать, что след ОСТАЁТСЯ.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.services.death_fingerprint import (  # noqa: E402
    BOOT, EXIT_CLEAN, arm, read_fingerprint,
)


def _child(body: str, log: Path) -> str:
    return textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, r"{REPO_ROOT}")
        from app.services.death_fingerprint import arm
        h = arm(r"{log}")
        {body}
    """).strip()


def _run_child(body: str, log: Path, *, wait: bool = True) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", _child(body, log)],
                            cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    if wait:
        proc.wait(timeout=60)
    return proc


def _boot_pids(log: Path) -> list[int]:
    """PID'ы из строк BOOT — по файлу, а НЕ по `Popen.pid`.

    Замер: `.venv\\Scripts\\python.exe` поднимает базовый интерпретатор, и
    `proc.pid` принадлежит запускающему звену, а не тому процессу, который
    вооружился. Сверять надо с тем, кто оставил след, иначе сторож проверяет
    не то (первый прогон дал `BOOT pid=896` при `proc.pid=284`).
    """
    if not log.is_file():
        return []
    return [int(ln.split("pid=")[1].split()[0])
            for ln in log.read_text(encoding="utf-8").splitlines()
            if ln.startswith(BOOT) and "pid=" in ln]


def _wait_for_boot(log: Path, *, timeout: float = 20.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pids = _boot_pids(log)
        if pids:
            return pids[-1]
        time.sleep(0.2)
    raise AssertionError(f"строка {BOOT} не появилась за {timeout}s: {log}")


def test_a_clean_exit_leaves_the_clean_marker(tmp_path):
    """Штатный выход обязан быть ОТЛИЧИМ от всего остального: иначе внешнее
    убийство не докажешь — «нет метки» читается только рядом с «метка бывает»."""
    log = tmp_path / "bot_death.log"
    _run_child("sys.exit(0)", log)
    pid = _boot_pids(log)[-1]

    text = log.read_text(encoding="utf-8")
    assert f"{EXIT_CLEAN} pid={pid}" in text, text
    assert read_fingerprint(log, pid=pid) == "clean"


def test_a_hard_crash_leaves_a_traceback(tmp_path):
    """Жёсткий крах интерпретатора (access violation) Python-исключением не
    ловится и в stderr приложения не попадает — ровно поэтому нужен
    faulthandler, пишущий в свой fd."""
    log = tmp_path / "bot_death.log"
    _run_child("import faulthandler; faulthandler._sigsegv()", log)
    pid = _boot_pids(log)[-1]

    text = log.read_text(encoding="utf-8")
    assert "Current thread" in text or "Stack" in text or "Fatal" in text, text
    assert read_fingerprint(log, pid=pid) == "crash"
    assert f"{EXIT_CLEAN} pid={pid}" not in text, (
        "крах не имеет права выглядеть чистым выходом")


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /F — Windows")
def test_an_external_kill_leaves_only_the_boot_line(tmp_path):
    """САМЫЙ ВАЖНЫЙ из трёх: именно так выглядит `TerminateProcess`.

    Отпечаток здесь — ОТСУТСТВИЕ следа. Он читается только потому, что строка
    BOOT осталась в том же файле: если файл перезаписывать при старте (как
    делал `Start-Process -RedirectStandardError` до 17.08), внешнее убийство
    станет неотличимо от «ничего не случилось».
    """
    log = tmp_path / "bot_death.log"
    proc = _run_child("time.sleep(60)", log, wait=False)
    try:
        pid = _wait_for_boot(log)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    text = log.read_text(encoding="utf-8")
    assert f"{BOOT} pid={pid}" in text, text
    assert EXIT_CLEAN not in text, "убитый снаружи процесс не мог отметиться чисто"
    assert read_fingerprint(log, pid=pid) == "killed"


def test_the_file_is_appended_so_the_previous_death_survives(tmp_path):
    """Ловит: перезапись, из-за которой «нет следа» становится ложью.

    Тот же класс, что затирание `bot_boot.stderr` при каждом запуске: следующий
    подъём уничтожал улику того, кто только что умер.
    """
    log = tmp_path / "bot_death.log"
    _run_child("sys.exit(0)", log)
    first = _boot_pids(log)[-1]
    _run_child("sys.exit(0)", log)
    pids = _boot_pids(log)

    assert first in pids, "первая смерть затёрта вторым запуском"
    assert len(pids) == 2, f"в файле не две записи о подъёме, а {pids}"


def test_two_processes_do_not_confuse_each_others_verdicts(tmp_path):
    """Ловит: разбор, который читает файл целиком.

    Гардиан поднимает бота заново через секунду; если вердикт по PID видит
    чужие строки, «убит» и «упал» перепутаются на первом же перезапуске.
    """
    log = tmp_path / "bot_death.log"
    _run_child("import faulthandler; faulthandler._sigsegv()", log)
    crashed = _boot_pids(log)[-1]
    _run_child("sys.exit(0)", log)
    clean = _boot_pids(log)[-1]

    assert crashed != clean, "два прогона получили один PID — тест ничего не различает"
    assert read_fingerprint(log, pid=crashed) == "crash"
    assert read_fingerprint(log, pid=clean) == "clean"


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /F — Windows")
def test_a_killed_process_stays_killed_even_if_the_next_one_crashes(tmp_path):
    """Ловит: чужой след, зачтённый предыдущему процессу.

    Именно эта пара и живёт в проде: гардиан убивает бота и через секунду
    поднимает следующего. Если разбор не закрывает окно процесса следующим
    BOOT, трассировка НОВОГО падения зачтётся СТАРОМУ, и «убили снаружи»
    навсегда прочитается как «упал сам» — то есть починят не то.

    Мутационный гейт нашёл этот пробел: пара «крах, потом чистый выход»
    оставалась зелёной и на снятом окне, потому что вердикт «crash» там
    получался по своей же трассировке. Различает только этот порядок.
    """
    log = tmp_path / "bot_death.log"
    victim = _run_child("time.sleep(60)", log, wait=False)
    try:
        killed_pid = _wait_for_boot(log)
        subprocess.run(["taskkill", "/PID", str(killed_pid), "/T", "/F"],
                       capture_output=True, check=False)
        victim.wait(timeout=30)
    finally:
        if victim.poll() is None:
            victim.kill()

    _run_child("import faulthandler; faulthandler._sigsegv()", log)
    crashed_pid = _boot_pids(log)[-1]

    assert crashed_pid != killed_pid, "PID совпали — тест ничего не различает"
    assert read_fingerprint(log, pid=crashed_pid) == "crash"
    assert read_fingerprint(log, pid=killed_pid) == "killed", (
        "трассировка СЛЕДУЮЩЕГО процесса зачтена убитому — разбор укажет на "
        "крах там, где было внешнее убийство")


def test_arming_never_takes_the_process_down(tmp_path):
    """Ловит: диагностику, ставшую условием старта.

    Каталог недоступен, путь занят, диск полон — бот обязан подняться всё
    равно. Он важнее, чем знание о его смерти.
    """
    blocked = tmp_path / "file_instead_of_dir"
    blocked.write_text("я файл, а не каталог", encoding="utf-8")

    assert arm(blocked / "logs" / "bot_death.log") is None


def test_an_unknown_pid_is_unknown_not_killed(tmp_path):
    """Ловит: вердикт «убит», выданный по отсутствию данных.

    «Не нашли записей» и «нашли BOOT без следа» — разные показания, и первое
    не имеет права выглядеть как второе.
    """
    log = tmp_path / "bot_death.log"
    _run_child("sys.exit(0)", log)

    assert read_fingerprint(log, pid=999999) == "unknown"
    assert read_fingerprint(tmp_path / "нет-файла.log", pid=1) == "unknown"


def test_the_bot_arms_the_fingerprint_before_anything_else():
    """Ловит: вооружение ПОСЛЕ первой возможной точки выхода.

    `main()` начинается с двух локов, каждый из которых зовёт `sys.exit(1)`.
    Вооружиться после них значит не увидеть ровно тот случай, когда бот умер
    на старте. Проверка текстовая: поднять настоящий `main()` в гейте нельзя.
    """
    src = (REPO_ROOT / "tools" / "jarvis_smart_telegram_control.py").read_text(
        encoding="utf-8")
    body = src[src.index("def main() -> None:"):]
    body = body[:body.index("def _main_inner")]

    assert "death_fingerprint" in body, "бот не вооружает отпечаток вовсе"
    assert body.index("death_fingerprint") < body.index("_acquire_single_instance_lock"), (
        "отпечаток вооружается после первого возможного sys.exit — смерть на "
        "старте останется без следа")
