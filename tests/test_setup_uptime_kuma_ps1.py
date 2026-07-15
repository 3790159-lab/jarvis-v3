# -*- coding: utf-8 -*-
"""Tests for scripts/setup_uptime_kuma.ps1 (DEV-17: monitoring & alerts).

Same dot-source-with-a-test-hook pattern as tests/test_setup_tailscale_channel_ps1.py:
dot-source the real .ps1 with -NoAutoRun (functions only - no real docker CLI
call, no real container brought up), then shadow the script's shell-out
choke points (Test-DockerAvailable, Invoke-DockerCompose, Test-KumaHttp) to
drive Get-KumaStatus's pure classification logic without ever touching a
real Docker/Kuma install. Actually bringing the stack up (docker compose up
-d) requires Docker Desktop running and is done by Daniil interactively -
see docs/UPTIME_KUMA_SETUP.md.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="setup_uptime_kuma.ps1 is Windows-only (PowerShell)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "setup_uptime_kuma.ps1"


def _run_ps(body: str, timeout: int = 40) -> subprocess.CompletedProcess:
    command = f". '{SCRIPT}' -NoAutoRun\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def test_dot_sourcing_with_noautorun_does_not_touch_docker():
    body = """
        function Invoke-DockerCompose { param([string[]]$ArgList) throw 'Invoke-DockerCompose should not be called' }
        function Test-DockerAvailable { throw 'Test-DockerAvailable should not be called' }
        'still-alive'
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "still-alive"


def test_top_level_params_are_status_down_noautorun_only():
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         f"(Get-Command '{SCRIPT}').Parameters.Keys -join ','"],
        capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    declared = {p.strip() for p in result.stdout.strip().splitlines()[-1].split(",")}
    assert declared == {"Status", "Down", "NoAutoRun"}


# ---------------------------------------------------------------------------
# Get-KumaStatus - pure combination of the three choke points. Fed fakes -
# no real docker/Kuma needed.
# ---------------------------------------------------------------------------

def test_status_docker_unavailable_short_circuits():
    body = """
        function Test-DockerAvailable { $false }
        function Invoke-DockerCompose { param([string[]]$ArgList) throw 'must not be called when docker is unavailable' }
        function Test-KumaHttp { throw 'must not be called when docker is unavailable' }
        $r = Get-KumaStatus
        "$($r.DockerAvailable),$($r.ContainerUp),$($r.HttpOk)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False,False,False"


def test_status_container_not_running_skips_http_probe():
    body = """
        function Test-DockerAvailable { $true }
        function Invoke-DockerCompose { param([string[]]$ArgList) @('some-other-service') }
        function Test-KumaHttp { throw 'must not be called when the container is not up' }
        $r = Get-KumaStatus
        "$($r.DockerAvailable),$($r.ContainerUp),$($r.HttpOk)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True,False,False"


def test_status_all_healthy():
    body = """
        function Test-DockerAvailable { $true }
        function Invoke-DockerCompose { param([string[]]$ArgList) @('uptime-kuma') }
        function Test-KumaHttp { $true }
        $r = Get-KumaStatus
        "$($r.DockerAvailable),$($r.ContainerUp),$($r.HttpOk)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True,True,True"


def test_status_container_up_but_http_unreachable():
    body = """
        function Test-DockerAvailable { $true }
        function Invoke-DockerCompose { param([string[]]$ArgList) @('uptime-kuma') }
        function Test-KumaHttp { $false }
        $r = Get-KumaStatus
        "$($r.DockerAvailable),$($r.ContainerUp),$($r.HttpOk)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True,True,False"
