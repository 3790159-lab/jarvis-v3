# -*- coding: utf-8 -*-
"""Real WSL+tmux integration test for scripts/start_cc.ps1 (DEV-1).

Per the DEV-1 task spec: any test that actually raises a real WSL/tmux
instance must be marked `integration` and SKIP (not FAIL) in the sanitized
regression gate - liveness of the session-survives-SSH-drop behavior is
accepted manually by Daniil, not by CI. Opt in locally with
CC_SESSION_INTEGRATION_TESTS=1 (mirrors RUNPOD_INTEGRATION_TESTS in
tests/test_runpod_integration.py).

Money-safety: this test never invokes the real `claude` CLI - it launches a
harmless `sleep`-style placeholder command inside the tmux session (via
start_cc.ps1's -Command override) so there is zero chance of an accidental
paid API call or an interactive TUI hanging the test.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="start_cc.ps1 is Windows-only (PowerShell + WSL)",
    ),
    pytest.mark.skipif(
        not os.getenv("CC_SESSION_INTEGRATION_TESTS"),
        reason="set CC_SESSION_INTEGRATION_TESTS=1 to enable (spins up a real WSL/tmux session)",
    ),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "start_cc.ps1"
DISTRO = os.getenv("CC_SESSION_TEST_DISTRO", "Ubuntu")


def _run_ps(args: list[str], timeout: int = 40) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT), *args],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def _has_session(session: str) -> bool:
    result = subprocess.run(
        ["wsl.exe", "-d", DISTRO, "--", "tmux", "has-session", "-t", session],
        capture_output=True, timeout=15,
    )
    return result.returncode == 0


def _kill_session(session: str) -> None:
    subprocess.run(
        ["wsl.exe", "-d", DISTRO, "--", "tmux", "kill-session", "-t", session],
        capture_output=True, timeout=15,
    )


def _read_marker(marker_path: str) -> str:
    result = subprocess.run(
        ["wsl.exe", "-d", DISTRO, "--", "cat", marker_path],
        capture_output=True, text=True, timeout=15,
    )
    return result.stdout.strip()


def test_session_survives_detach_and_reattach_picks_up_same_process():
    """The core DEV-1 acceptance criterion, automated: a tmux session created
    by start_cc.ps1 keeps its process alive after the "client" goes away
    (simulating an SSH drop), and a second start_cc.ps1 invocation reattaches
    to the SAME session instead of creating a new one.

    Same-process identity is proven via a marker file the pane's startup
    command writes exactly once (a nanosecond timestamp) - if start_cc.ps1
    were to recreate the session instead of reattaching, the marker would be
    rewritten with a new timestamp. (An earlier version of this test compared
    `tmux list-panes -F '#{pane_pid}'` output instead: wsl.exe mangles any
    argument containing '#' - it never reaches tmux, which then fails with
    "-F expects an argument". The marker-file approach sidesteps '#' entirely.)
    """
    session = f"cc-test-{uuid.uuid4().hex[:8]}"
    marker = f"/tmp/{session}.marker"
    command = f"date +%s%N > {marker}; sleep 300"
    try:
        # -NoAttach: create the session but don't block this test on an
        # interactive terminal attach.
        result = _run_ps([
            "-Distro", DISTRO, "-Session", session,
            "-WorkDir", "/tmp", "-Command", command, "-NoAttach",
        ])
        assert result.returncode == 0, result.stderr
        assert _has_session(session), "tmux session was not created"

        before = _read_marker(marker)
        assert before, "marker file was not written by the session's startup command"

        # Simulate an SSH drop: nothing attached to the session, no client
        # process alive on the Windows side. The tmux SERVER (inside the WSL2
        # VM) and the `sleep 300` process inside it must still be running.
        time.sleep(2)
        assert _has_session(session), "tmux session died with no client attached"

        # Reconnect: running start_cc.ps1 again for the same session must
        # reattach (no new-session), proving the original process is what a
        # real reconnect would pick back up - not a freshly spawned one.
        result2 = _run_ps([
            "-Distro", DISTRO, "-Session", session,
            "-WorkDir", "/tmp", "-Command", command, "-NoAttach",
        ])
        assert result2.returncode == 0, result2.stderr

        after = _read_marker(marker)
        assert before == after, (
            f"reattach rewrote the marker (before={before!r}, after={after!r}) "
            "- session was recreated instead of reused"
        )
    finally:
        _kill_session(session)
        subprocess.run(["wsl.exe", "-d", DISTRO, "--", "rm", "-f", marker], capture_output=True)
