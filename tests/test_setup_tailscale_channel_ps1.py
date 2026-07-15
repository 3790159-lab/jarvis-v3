# -*- coding: utf-8 -*-
"""Tests for scripts/remote/setup_tailscale_channel.ps1 (DEV-15: second
independent remote-access channel, not dependent on cloudflared).

Same dot-source-with-a-test-hook pattern as tests/test_start_cc_ps1.py and
tests/test_add_secret_ps1.py: dot-source the real .ps1 with -NoAutoRun
(functions only - no real winget/tailscale CLI calls, no real service
changes), then shadow the script's shell-out choke points (Get-TailscaleService,
Invoke-Tailscale, Invoke-Winget) to drive the pure classification logic
without ever touching a real Tailscale install. Actually installing/logging
in requires a real machine + browser + admin rights and is done by Daniil
interactively - see docs/REMOTE_ACCESS_SECOND_CHANNEL.md.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="setup_tailscale_channel.ps1 is Windows-only (PowerShell)",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "remote" / "setup_tailscale_channel.ps1"


def _run_ps(body: str, timeout: int = 40) -> subprocess.CompletedProcess:
    """Dot-source setup_tailscale_channel.ps1 with -NoAutoRun (functions
    only - never shells out to winget/tailscale or touches a real service on
    its own), then run `body` in the same session."""
    command = f". '{SCRIPT}' -NoAutoRun\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def test_dot_sourcing_with_noautorun_does_not_install_or_call_cli():
    # Dot-sourcing with -NoAutoRun must define functions only - no winget
    # install, no `tailscale up`, no service changes - so tests (and a stray
    # `. .\\setup_tailscale_channel.ps1` in a REPL) never risk mutating a
    # real machine.
    body = """
        function Invoke-Winget { param([string[]]$ArgList) throw 'Invoke-Winget should not be called' }
        function Invoke-Tailscale { param([string[]]$ArgList) throw 'Invoke-Tailscale should not be called' }
        'still-alive'
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "still-alive"


def test_top_level_params_are_status_uninstall_noautorun_only():
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         f"(Get-Command '{SCRIPT}').Parameters.Keys -join ','"],
        capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr
    declared = {p.strip() for p in result.stdout.strip().splitlines()[-1].split(",")}
    assert declared == {"Status", "Uninstall", "NoAutoRun"}


# ---------------------------------------------------------------------------
# Test-TailscaleServiceHealth - pure classification of a Get-Service-shaped
# object (or $null). Fed fake pscustomobjects - no real service needed.
# ---------------------------------------------------------------------------

def test_service_health_not_installed_when_service_missing():
    body = """
        $r = Test-TailscaleServiceHealth -Service $null
        "$($r.Installed),$($r.Running),$($r.AutoStart),$($r.Healthy)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False,False,False,False"


def test_service_health_unhealthy_when_stopped():
    body = """
        $svc = [pscustomobject]@{ Status = 'Stopped'; StartType = 'Automatic' }
        $r = Test-TailscaleServiceHealth -Service $svc
        "$($r.Installed),$($r.Running),$($r.AutoStart),$($r.Healthy)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True,False,True,False"


def test_service_health_unhealthy_when_manual_start():
    body = """
        $svc = [pscustomobject]@{ Status = 'Running'; StartType = 'Manual' }
        $r = Test-TailscaleServiceHealth -Service $svc
        "$($r.Installed),$($r.Running),$($r.AutoStart),$($r.Healthy)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True,True,False,False"


def test_service_health_healthy_when_running_and_automatic():
    body = """
        $svc = [pscustomobject]@{ Status = 'Running'; StartType = 'Automatic' }
        $r = Test-TailscaleServiceHealth -Service $svc
        "$($r.Installed),$($r.Running),$($r.AutoStart),$($r.Healthy)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True,True,True,True"


# ---------------------------------------------------------------------------
# Get-TailscaleConnectionStatus - pure parse of `tailscale status` text.
# ---------------------------------------------------------------------------

def test_connection_status_logged_in_for_normal_status_output():
    body = """
        $r = Get-TailscaleConnectionStatus -StatusText "100.101.102.103 pc-loe  admin@  windows  -`n"
        "$($r.LoggedIn)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True"


def test_connection_status_logged_out_detected():
    body = """
        $r = Get-TailscaleConnectionStatus -StatusText "Logged out.`n"
        "$($r.LoggedIn)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_connection_status_needs_login_detected():
    body = """
        $r = Get-TailscaleConnectionStatus -StatusText "NeedsLogin`n"
        "$($r.LoggedIn)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


# ---------------------------------------------------------------------------
# Get-SecondChannelStatus - composes the service + CLI checks. Shadows
# Get-TailscaleService and Invoke-Tailscale (the script's only two shell-out
# choke points) so no real service/CLI is ever touched.
# ---------------------------------------------------------------------------

def test_second_channel_status_skips_cli_call_when_service_missing():
    body = """
        function Get-TailscaleService { $null }
        $global:cliCalls = 0
        function Invoke-Tailscale { param([string[]]$ArgList) $global:cliCalls++; 'unused' }
        $r = Get-SecondChannelStatus
        "$($r.Installed),$($r.Healthy),$global:cliCalls"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False,False,0"


def test_second_channel_status_healthy_end_to_end():
    body = """
        function Get-TailscaleService { [pscustomobject]@{ Status = 'Running'; StartType = 'Automatic' } }
        function Invoke-Tailscale { param([string[]]$ArgList) '100.101.102.103 pc-loe  admin@  windows  -' }
        $r = Get-SecondChannelStatus
        "$($r.Installed),$($r.Running),$($r.AutoStart),$($r.LoggedIn),$($r.Healthy)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "True,True,True,True,True"


def test_second_channel_status_unhealthy_when_logged_out_despite_service_running():
    body = """
        function Get-TailscaleService { [pscustomobject]@{ Status = 'Running'; StartType = 'Automatic' } }
        function Invoke-Tailscale { param([string[]]$ArgList) 'Logged out.' }
        $r = Get-SecondChannelStatus
        "$($r.Healthy),$($r.Reason)"
    """
    result = _run_ps(body)
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip().splitlines()[-1]
    assert out.startswith("False,")
