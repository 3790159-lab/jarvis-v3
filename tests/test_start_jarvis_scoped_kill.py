"""DEV-38: стартер обязан убивать ТОЛЬКО бота своего дерева, и ни один тест
не имеет права запустить боевой стартер.

Замер, из которого это выросло (18.08 19:25:04, ETW):

    NtTerminateProcess | цель PID 760 (ЖИВОЙ прод-бот из C:\\jarvis)
      инициатор PID 13068: powershell -File
      C:\\jarvis_worktrees\\dev36-c7\\start_jarvis.ps1 -BotOnly
      (ppid 11936 = воркер pytest)

То есть pytest из worktree запускал ТАМОШНИЙ стартер, а тот сносил бота из
ДРУГОГО дерева, потому что отбирал жертв по глобальной подстроке
'jarvis_smart_telegram_control' без скоупа на свой $ProjectRoot. ~16 прогонов
полного гейта в рабочий день = ~16 смертей бота в сутки.

Здесь ДВЕ половины и на каждую свой сторож:

* половина 1 — тест, который реально запускает стартер: ловится сторожем
  класса в tests/conftest.py (проверяется ниже, живьём);
* половина 2 — стартер, который сносит чужого бота: ловится воспроизведением
  инцидента на ДВУХ соседних tmp-деревьях, без единого настоящего бота.

Pester намеренно не вводится (как и в test_bot_guardian_stop_old_bot.py):
стартер точечно-сорсится через powershell.exe с -Root на tmp и -NoLaunch.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="стартер только под Windows (PowerShell + Win32_Process)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STARTER = REPO_ROOT / "start_jarvis.ps1"


def _run_ps(body: str, root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    """Точечно засорсить стартер на tmp-корне (-NoLaunch: ничего не запускать,
    никого не убивать) и выполнить body в той же сессии.

    -Root ОБЯЗАТЕЛЕН: без него сторож класса из conftest не пропустит вызов —
    и это правильно, потому что без него стартер целился бы в живое дерево.
    """
    command = ". '{0}' -Root '{1}' -NoLaunch\n{2}\n".format(STARTER, root, body)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        # Кодировку пиним: под PYTHONUTF8=1 (среда мерж-гейта) наивный text=True
        # пытается utf-8-декодировать вывод в OEM-кодировке и падает. Токены, по
        # которым мы судим ("0"/"1"), ascii — их errors="replace" не портит.
        encoding="utf-8", errors="replace",
    )


def _pid_alive(pid: int) -> bool:
    # BYTES, не text=True: tasklist печатает в OEM-кодировке консоли (cp866),
    # строгий utf-8-декод под PYTHONUTF8=1 на этом падает. Совпадение по ascii-
    # цифрам в сырых байтах от кодовой страницы не зависит.
    r = subprocess.run(["tasklist", "/FI", "PID eq {0}".format(pid)],
                       capture_output=True, timeout=10)
    return str(pid).encode("ascii") in (r.stdout or b"")


def _spawn_fake_bot(root: Path) -> subprocess.Popen:
    """Поднять процесс формы прода: python.exe, в командной строке — корень и
    имя jarvis_smart_telegram_control."""
    bot = root / "tools" / "jarvis_smart_telegram_control.py"
    bot.parent.mkdir(parents=True, exist_ok=True)
    bot.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(bot)])
    deadline = time.time() + 10
    while not _pid_alive(proc.pid) and time.time() < deadline:
        time.sleep(0.2)
    assert _pid_alive(proc.pid), "подставной бот не поднялся"
    return proc


def _kill(proc: subprocess.Popen) -> None:
    if _pid_alive(proc.pid):
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


@pytest.fixture
def two_roots(tmp_path: Path):
    """Два СОСЕДНИХ корня: ни один не префикс другого. Именно так соотносятся
    C:\\jarvis и C:\\jarvis_worktrees\\* — на вложенных путях сторож был бы
    слабее, чем инцидент."""
    a = tmp_path / "tree_a"
    b = tmp_path / "tree_b"
    for r in (a, b):
        (r / "state").mkdir(parents=True)
    return a, b


# ---------------------------------------------------------------------------
# половина 2: стартер не трогает чужого бота
# ---------------------------------------------------------------------------

def test_starter_from_another_root_leaves_the_foreign_bot_alive(two_roots):
    """ВОСПРОИЗВЕДЕНИЕ ИНЦИДЕНТА. Stop-OldBot из tree_a обязан оставить бота
    tree_b ЖИВЫМ. До починки глобальный match убивал его — ровно так worktree
    убивал прод."""
    root_a, root_b = two_roots
    victim = _spawn_fake_bot(root_b)
    try:
        result = _run_ps("Stop-OldBot | Out-Null; Write-Output DONE", root_a)
        assert result.returncode == 0, result.stderr
        assert "DONE" in result.stdout, result.stdout

        # Даём время умереть, если стартер всё-таки выстрелил: сторож обязан
        # ловить убийство, а не выигрывать гонку.
        deadline = time.time() + 5
        while _pid_alive(victim.pid) and time.time() < deadline:
            time.sleep(0.3)
        assert _pid_alive(victim.pid), (
            "стартер из tree_a снёс бота из tree_b — это и есть DEV-38: "
            "отбор жертв не скоуплен по $ProjectRoot"
        )
    finally:
        _kill(victim)


def test_starter_still_kills_the_bot_of_its_own_root(two_roots):
    """Обратная половина сторожа: скоуп не имеет права выродиться в «никогда
    никого». Stop-OldBot из tree_b обязан снести СВОЕГО бота."""
    _root_a, root_b = two_roots
    own = _spawn_fake_bot(root_b)
    try:
        result = _run_ps("Stop-OldBot | Out-Null; Write-Output DONE", root_b)
        assert result.returncode == 0, result.stderr

        deadline = time.time() + 15
        while _pid_alive(own.pid) and time.time() < deadline:
            time.sleep(0.3)
        assert not _pid_alive(own.pid), (
            "стартер не убил бота СВОЕГО дерева — скоуп затянут слишком туго"
        )
    finally:
        _kill(own)


def test_get_bot_processes_is_scoped_to_project_root(two_roots):
    """Тот же контракт на уровне отбора, без убийства: tree_a не видит бота
    tree_b, а tree_b своего — видит (иначе «0» ничего не доказывает)."""
    root_a, root_b = two_roots
    victim = _spawn_fake_bot(root_b)
    try:
        r_a = _run_ps("@(Get-BotProcesses).Count", root_a)
        assert r_a.returncode == 0, r_a.stderr
        assert r_a.stdout.strip().splitlines()[-1].strip() == "0", (
            "стартер tree_a увидел бота tree_b:\n{0}".format(r_a.stdout)
        )

        body = "@(Get-BotProcesses | Where-Object {{ $_.ProcessId -eq {0} }}).Count".format(victim.pid)
        r_b = _run_ps(body, root_b)
        assert r_b.returncode == 0, r_b.stderr
        assert r_b.stdout.strip().splitlines()[-1].strip() == "1", (
            "стартер tree_b не увидел СВОЕГО бота:\n{0}".format(r_b.stdout)
        )
    finally:
        _kill(victim)


def test_the_old_global_predicate_would_have_matched_the_foreign_bot(two_roots):
    """Сторож выше не имеет права быть пустым: надо показать, что стенд ВООБЩЕ
    способен различить: до починки бот tree_b попадал в жертвы стартеру tree_a.

    ПРЕДИКАТ, НЕ УБИЙСТВО. Старый отбор глобален — он матчит и НАСТОЯЩЕГО бота
    из C:\\jarvis. Прогнать здесь старый Stop-OldBot значило бы воспроизвести
    инцидент на живом проде, поэтому сравниваем ровно СОСТАВ отобранного, ни
    одного Stop-Process.
    """
    root_a, root_b = two_roots
    victim = _spawn_fake_bot(root_b)
    try:
        body = (
            "$old = @(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" "
            "| Where-Object {{ $_.CommandLine -and $_.CommandLine -match "
            "'jarvis_smart_telegram_control' }} "
            "| Where-Object {{ $_.ProcessId -eq {0} }}).Count\n"
            "$new = @(Get-BotProcesses | Where-Object {{ $_.ProcessId -eq {0} }}).Count\n"
            "Write-Output \"OLD:$old\"\n"
            "Write-Output \"NEW:$new\"\n"
        ).format(victim.pid)
        r = _run_ps(body, root_a)
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "OLD:1" in out, (
            "стенд не воспроизводит инцидент: старый глобальный отбор НЕ увидел "
            "чужого бота, значит зелёный сторож выше ничего не доказывает:\n" + out
        )
        assert "NEW:0" in out, (
            "починка не сработала: скоупленный отбор всё ещё видит чужого бота:\n" + out
        )
    finally:
        _kill(victim)


def test_starter_has_no_unscoped_command_line_match_left():
    """Статический дубль: в стартере не должно остаться ни одного отбора по
    голой подстроке. Дешёвая проверка против повторного заведения дыры рядом —
    живые тесты выше стерегут ОДНУ точку, эта стережёт файл."""
    text = STARTER.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if "jarvis_smart_telegram_control" not in line:
            continue
        if "CommandLine" not in line:
            continue
        assert "$ProjectRoot" in line, (
            "start_jarvis.ps1:{0} отбирает процессы по командной строке "
            "без скоупа на $ProjectRoot:\n{1}".format(lineno, line)
        )


# ---------------------------------------------------------------------------
# половина 1: ни один тест не запускает боевой стартер
# ---------------------------------------------------------------------------

def test_conftest_guard_blocks_the_prod_shaped_launch():
    """Сторож класса обязан ПАДАТЬ на той самой командной строке, которой
    restart_bot_if_dead() убивал прод."""
    prod_argv = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(REPO_ROOT / "start_jarvis.ps1"), "-BotOnly",
    ]
    with pytest.raises(AssertionError, match="DEV-38"):
        subprocess.Popen(prod_argv)


def test_conftest_guard_blocks_it_through_subprocess_run():
    """Та же дыра через run(): подмена одного Popen обязана накрывать и его."""
    with pytest.raises(AssertionError, match="DEV-38"):
        subprocess.run(["powershell", "-File", str(REPO_ROOT / "start_jarvis.ps1"), "-BotOnly"])


def test_conftest_guard_allows_a_root_scoped_launch(tmp_path):
    """И не имеет права мешать честному вызову на tmp-дереве — иначе сторож
    просто запретил бы себя же проверять."""
    command = ". '{0}' -Root '{1}' -NoLaunch; Write-Output OK".format(STARTER, tmp_path)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", command],
        capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_conftest_guard_leaves_unrelated_subprocesses_alone():
    """Сторож стоит на ОДНОЙ примете. Всё прочее обязано работать как работало —
    иначе он развалит половину набора и его снимут."""
    r = subprocess.run([sys.executable, "-c", "print('hi')"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "hi" in r.stdout
