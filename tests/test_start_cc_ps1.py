# -*- coding: utf-8 -*-
"""Tests for scripts/start_cc.ps1 (DEV-1: persistent CC terminal via WSL+tmux).

Two tiers, mirroring tests/test_bot_guardian_stop_old_bot.py:

1. Logic tests (this module, run in the sanitized regression gate): dot-source
   the real .ps1 with -NoAutoRun (test hook - define functions only, never
   shell out to wsl.exe on its own), then shadow Invoke-Wsl - the script's one
   choke point for shelling out to wsl.exe - to record calls and fake exit
   codes. Never touches a real WSL/tmux instance.

2. Real WSL+tmux integration test, gated behind CC_SESSION_INTEGRATION_TESTS=1
   and pytest.mark.integration (per DEV-1 spec: any test that actually raises
   WSL/tmux must SKIP, not FAIL, in the sanitized gate - live session
   persistence is accepted manually, not by CI). See
   test_start_cc_ps1_integration.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="start_cc.ps1 is Windows-only (PowerShell + WSL)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "start_cc.ps1"


def _run_ps(body: str, timeout: int = 40) -> subprocess.CompletedProcess:
    """Dot-source start_cc.ps1 with -NoAutoRun (functions only, no real
    wsl.exe calls at all), then run `body` in the same session."""
    command = f". '{SCRIPT}' -NoAutoRun\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


# Fakes Invoke-Wsl and logs every call to a global array, so the PowerShell
# body can assert on exactly what would have been shelled out to wsl.exe.
_FAKE_INVOKE_WSL_PRELUDE = """
$global:calls = New-Object System.Collections.ArrayList
function Invoke-Wsl {
    param([string]$Distro, [string[]]$ArgList)
    [void]$global:calls.Add(@{ Distro = $Distro; Args = ($ArgList -join ' ') })
    return $global:HasSessionExitCode
}
"""


def test_creates_new_session_when_none_exists():
    body = _FAKE_INVOKE_WSL_PRELUDE + """
        $global:HasSessionExitCode = 1  # has-session fails -> no existing session
        Start-Cc -Distro 'Ubuntu' -Session 'cc' -WorkDir '/mnt/c/jarvis' -Command 'claude' -NoAttach
        @($global:calls | Where-Object { $_.Args -like 'tmux new-session*' }).Count
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "1"


def test_reattaches_without_recreating_when_session_exists():
    body = _FAKE_INVOKE_WSL_PRELUDE + """
        $global:HasSessionExitCode = 0  # has-session succeeds -> already running
        Start-Cc -Distro 'Ubuntu' -Session 'cc' -WorkDir '/mnt/c/jarvis' -Command 'claude' -NoAttach
        @($global:calls | Where-Object { $_.Args -like 'tmux new-session*' }).Count
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_new_switch_kills_and_recreates_existing_session():
    body = _FAKE_INVOKE_WSL_PRELUDE + """
        $global:HasSessionExitCode = 0  # session exists
        Start-Cc -Distro 'Ubuntu' -Session 'cc' -WorkDir '/mnt/c/jarvis' -Command 'claude' -New -NoAttach
        # NOTE: not named $new/$kill - dot-sourcing start_cc.ps1 leaks its typed
        # [switch]$New script parameter into this scope, and PowerShell variable
        # names are case-insensitive, so a local $new would collide with it and
        # throw trying to coerce an Int32 into a SwitchParameter.
        $killCount = @($global:calls | Where-Object { $_.Args -like 'tmux kill-session*' }).Count
        $newSessionCount = @($global:calls | Where-Object { $_.Args -like 'tmux new-session*' }).Count
        "$killCount,$newSessionCount"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "1,1"


def test_does_not_attach_when_noattach_passed():
    body = _FAKE_INVOKE_WSL_PRELUDE + """
        $global:HasSessionExitCode = 0
        Start-Cc -Distro 'Ubuntu' -Session 'cc' -WorkDir '/mnt/c/jarvis' -Command 'claude' -NoAttach
        @($global:calls | Where-Object { $_.Args -like 'tmux attach*' }).Count
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"


def test_dot_sourcing_with_noautorun_does_not_auto_run():
    # Dot-sourcing the script with -NoAutoRun must define functions only - it
    # must NOT call Start-Cc (and therefore not Invoke-Wsl) on its own, same
    # as -NoLoop in scripts/bot_guardian_detached.ps1.
    body = _FAKE_INVOKE_WSL_PRELUDE + """
        $global:HasSessionExitCode = 1
        $global:calls.Count
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0"
