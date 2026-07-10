"""Integration tests for scripts/bot_guardian_detached.ps1 Layers B+C
(Этап 1, хвост #1 of docs/MASTER-PLAN.md).

Pester is deliberately not introduced here (per task spec); these are plain
pytest tests that dot-source the real .ps1 file (with -Root pointed at a tmp
dir and -NoLoop so no lock/loop is taken) and drive its functions through
powershell.exe subprocesses. Layer B is exercised against a REAL fake
long-lived process (mirroring the real bot's launch shape: a python.exe
running a script literally named jarvis_smart_telegram_control.py) rather than
a mock, per the task spec. The one branch that is impractical to trigger for
real on Windows (taskkill /F is TerminateProcess — it does not fail for a
process a normal user owns) is exercised by shadowing Get-BotProcesses after
dot-sourcing, a plain PowerShell scoping trick (not a mocking framework).
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="guardian script is Windows-only (PowerShell + taskkill + Win32_Process)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "bot_guardian_detached.ps1"


def _run_ps(body: str, root: Path, timeout: int = 40) -> subprocess.CompletedProcess:
    """Dot-source the guardian script against a tmp -Root (no lock, no loop),
    then run `body` in the same session so it can call the guardian's
    functions. Never touches C:\\jarvis."""
    command = (
        f". '{SCRIPT}' -Root '{root}' -NoLoop\n"
        f"{body}\n"
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        # Pin decoding so the test is hermetic to the ambient locale: under
        # PYTHONUTF8=1 (the merge-gate env) a naive text=True would try to utf-8-
        # decode PowerShell's OEM-codepage output (the guardian logs Cyrillic via
        # Write-Host) and raise. errors="replace" keeps the ascii tokens we assert
        # on ("True"/"False"/digits) intact regardless of codepage.
        encoding="utf-8", errors="replace",
    )


def _write_fake_bot(path: Path, spawn_child: bool) -> None:
    if spawn_child:
        # The child's stdout goes to DEVNULL (NOT the inherited pipe) so the
        # parent's stdout closes on its own death, and the pid line ends in a
        # NEWLINE — the reader uses readline(), which blocks forever without one
        # (this missing \n was the primary hang that got the suite auto-backgrounded
        # in task 6b132e). 30s lifetime: long enough to be killed live, short
        # enough not to linger for 5 min if a test ever leaks it.
        path.write_text(
            textwrap.dedent(
                """
                import subprocess, sys, time
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                sys.stdout.write(str(child.pid) + "\\n")
                sys.stdout.flush()
                time.sleep(30)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
    else:
        path.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    # BYTES, not text=True: tasklist prints in the OEM console codepage
    # (cp866/cp850 on RU Windows), which fails a strict utf-8 decode under
    # PYTHONUTF8=1 — the merge-gate env. That made result.stdout blow up and the
    # test go red in the gate while green in the worktree. Matching the ascii pid
    # in the raw bytes is codepage-independent (ascii is a subset of every OEM cp).
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, timeout=10
    )
    return str(pid).encode("ascii") in (result.stdout or b"")


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    (tmp_path / "state" / "logs").mkdir(parents=True)
    (tmp_path / "state" / "locks").mkdir(parents=True)
    return tmp_path


def test_stop_old_bot_kills_pid_file_process_and_its_child_tree(fake_root):
    """Layer B core contract: Stop-OldBot must taskkill /T (whole tree), not
    just the single PID — a plain Stop-Process on the parent (the pre-fix
    behaviour) leaves the child orphaned and alive."""
    fake_bot = fake_root / "jarvis_smart_telegram_control.py"
    _write_fake_bot(fake_bot, spawn_child=True)
    proc = subprocess.Popen(
        [sys.executable, str(fake_bot)], stdout=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",  # locale-independent (PYTHONUTF8 gate env)
    )
    try:
        child_pid_line = proc.stdout.readline().strip()
        assert child_pid_line, "fake bot did not report its child pid"
        child_pid = int(child_pid_line)
        assert _pid_alive(proc.pid)
        assert _pid_alive(child_pid)

        (fake_root / "state" / "bot.pid").write_text(str(proc.pid), encoding="utf-8")

        result = _run_ps("Stop-OldBot | Out-String | Write-Output", fake_root)
        assert result.returncode == 0, result.stderr
        assert "True" in result.stdout

        deadline = time.time() + 10
        while (_pid_alive(proc.pid) or _pid_alive(child_pid)) and time.time() < deadline:
            time.sleep(0.3)

        assert not _pid_alive(proc.pid), "parent bot process survived Stop-OldBot"
        assert not _pid_alive(child_pid), "child process survived Stop-OldBot (tree-kill missing)"
        assert not (fake_root / "state" / "bot.pid").exists(), "bot.pid must be removed only after confirmed death"
    finally:
        for p in (proc.pid, child_pid if child_pid_line else None):
            if p and _pid_alive(p):
                subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
        proc.wait(timeout=5)


def test_stop_old_bot_removes_pid_file_only_when_process_already_gone(fake_root):
    """Stale bot.pid pointing at an already-dead PID: Stop-OldBot should treat
    it as a clean win and remove the file."""
    (fake_root / "state" / "bot.pid").write_text("999999999", encoding="utf-8")

    result = _run_ps("Stop-OldBot | Out-String | Write-Output", fake_root)
    assert result.returncode == 0, result.stderr
    assert "True" in result.stdout
    assert not (fake_root / "state" / "bot.pid").exists()


def test_stop_old_bot_refuses_and_keeps_pid_file_when_bot_wont_die(fake_root):
    """Layer B safety net: if something still matches "a live bot process"
    after the kill attempts and the wait window, Stop-OldBot must return
    False and MUST NOT clear bot.pid — this is what stops Start-Bot from ever
    launching a second poller on top of a live one.

    A real user-owned process cannot be made to survive taskkill /F (it is
    TerminateProcess, not a signal a process can catch/ignore), so this
    specific branch is driven by shadowing Get-BotProcesses — plain
    PowerShell function scoping, not a mocking framework — to force "still
    alive" deterministically without touching any real process.
    """
    (fake_root / "state" / "bot.pid").write_text("123456789", encoding="utf-8")

    body = (
        "function Get-BotProcesses { [PSCustomObject]@{ ProcessId = 999999999 } }\n"
        "Stop-OldBot -MaxWaitSec 1 | Out-String | Write-Output\n"
    )
    result = _run_ps(body, fake_root)
    assert result.returncode == 0, result.stderr
    assert "False" in result.stdout
    assert (fake_root / "state" / "bot.pid").exists(), "bot.pid must survive when the bot won't die"


def test_start_bot_aborts_launch_when_old_bot_wont_die(fake_root):
    """Start-Bot must never launch a new bot on top of one Stop-OldBot could
    not confirm dead (never start on top of a live poller)."""
    (fake_root / "state" / "bot.pid").write_text("123456789", encoding="utf-8")

    body = (
        "function Get-BotProcesses { [PSCustomObject]@{ ProcessId = 999999999 } }\n"
        "Start-Bot | Out-String | Write-Output\n"
    )
    result = _run_ps(body, fake_root)
    assert result.returncode == 0, result.stderr
    assert "False" in result.stdout
    assert not (fake_root / "state" / "logs" / "bot_boot.stdout.log").exists(), (
        "Start-Bot must not spawn the bot process when the old one is still alive"
    )


def test_debounce_gates_relaunch_until_n_consecutive_failures(fake_root):
    """Layer C, exercised against the REAL shipped main loop (not -NoLoop):
    with the default-shaped DebounceFailures=3 and no bot process ever
    appearing (fake_root has no tools/jarvis_smart_telegram_control.py, so
    Test-Bot always fails), the guardian must log exactly two 'debouncing'
    cycles before it ever attempts a restart - never on the first failure."""
    log = fake_root / "state" / "logs" / "bot_guardian.stdout.log"
    proc = subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
            "-Root", str(fake_root), "-IntervalSeconds", "1", "-DebounceFailures", "3",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        first_restart_seen = False
        while time.time() < deadline:
            if log.exists() and "restarting" in log.read_text(encoding="utf-8", errors="replace"):
                first_restart_seen = True
                break
            time.sleep(0.5)
        assert first_restart_seen, "guardian never attempted a restart within 20s"
        text = log.read_text(encoding="utf-8", errors="replace")
        restart_idx = text.index("restarting")
        before = text[:restart_idx]
        assert before.count("debouncing") == 2, (
            f"expected exactly 2 debounce cycles before the 3rd-failure restart, got log:\n{text}"
        )
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=10)


def test_get_bot_processes_is_scoped_to_root_never_matches_a_bot_elsewhere(tmp_path):
    """Prod-safety regression (task 6b132e): Get-BotProcesses MUST be scoped to
    $Root, so a guardian with a temp root can never match — let alone taskkill —
    a jarvis_smart_telegram_control process living under a DIFFERENT root (e.g. the
    real prod bot under C:\\jarvis). Proven hermetically with two SIBLING temp
    roots (neither a path-prefix of the other), no real bot involved: the global
    substring match this replaces would have matched the bot under root_b from a
    guardian scoped to root_a, which is exactly what killed the live bot + hung.
    """
    root_a = tmp_path / "guardian_a"
    (root_a / "state" / "logs").mkdir(parents=True)
    (root_a / "state" / "locks").mkdir(parents=True)
    root_b = tmp_path / "elsewhere_b"
    root_b.mkdir()
    bot_b = root_b / "jarvis_smart_telegram_control.py"
    _write_fake_bot(bot_b, spawn_child=False)

    proc = subprocess.Popen([sys.executable, str(bot_b)])
    try:
        deadline = time.time() + 10
        while not _pid_alive(proc.pid) and time.time() < deadline:
            time.sleep(0.2)
        assert _pid_alive(proc.pid), "fake bot under root_b never came up"

        # a guardian scoped to root_a must NOT see the bot under root_b
        r_a = _run_ps("(@(Get-BotProcesses).Count)", root_a)
        assert r_a.returncode == 0, r_a.stderr
        assert r_a.stdout.strip().splitlines()[-1].strip() == "0", (
            f"guardian scoped to root_a matched a bot under root_b (global-match bug):\n{r_a.stdout}"
        )
        # sanity: a guardian scoped to root_b DOES see its own bot (scope works, not just always-empty)
        r_b = _run_ps(
            f"(@(Get-BotProcesses | Where-Object {{ $_.ProcessId -eq {proc.pid} }}).Count)", root_b
        )
        assert r_b.returncode == 0, r_b.stderr
        assert r_b.stdout.strip().splitlines()[-1].strip() == "1", (
            f"guardian scoped to its own root failed to see its own bot:\n{r_b.stdout}"
        )
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.wait(timeout=5)


def test_heartbeat_max_age_default_raised_above_90s(fake_root):
    body = "Write-Output \"HB:$HeartbeatMaxAgeSec\"\nWrite-Output \"DEBOUNCE:$DebounceFailures\""
    result = _run_ps(body, fake_root)
    assert result.returncode == 0, result.stderr
    hb_line = next(line for line in result.stdout.splitlines() if line.startswith("HB:"))
    hb_value = int(hb_line.split(":", 1)[1].strip())
    assert hb_value > 90, f"HeartbeatMaxAgeSec default should be raised above the old 90s, got {hb_value}"
    debounce_line = next(line for line in result.stdout.splitlines() if line.startswith("DEBOUNCE:"))
    assert int(debounce_line.split(":", 1)[1].strip()) >= 2
