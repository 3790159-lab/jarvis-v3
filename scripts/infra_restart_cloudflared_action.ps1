# One-shot ELEVATED action for /infra_restart's [cloudflared] button (DEV-12).
# Runs ONLY as the pre-registered JarvisInfraRestartCloudflared scheduled task
# (RunLevel Highest -- see register_infra_restart_tasks.ps1). The bot process
# only triggers this via `schtasks /Run /TN JarvisInfraRestartCloudflared`;
# Task Scheduler supplies the elevation Start-Service/Stop-Process need here,
# not the (possibly unelevated) caller.
#
# StopPending is the specific failure this exists for: a wedged StopPending
# service can make Start-Service/Stop-Service themselves hang, so a hung
# cloudflared.exe is force-killed first, THEN the service is (re)started.
# app.services.infra_control polls Get-Service itself afterward (a read, no
# elevation needed) -- this script does not report back on its own.

$ErrorActionPreference = 'Continue'

# DEV-59: declare the console encoding as UTF-8 -- the READER side. Under Task
# Scheduler the console starts in cp866 (measured 24.08 with a probe task, and
# the setter does NOT throw there even with no console attached).
# This script prints nothing today, and that is exactly why the declaration
# goes in anyway: the park grants no exemption for "nothing to break today" --
# tomorrow the file grows a Cyrillic message and such an exemption rots
# silently. No mediator (PYTHONUTF8) here: this script starts no python.
# ASCII only on purpose -- this file carries no BOM, so PS 5.1 decodes it as
# cp1251 and a Cyrillic comment would land here as mojibake.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$svc = Get-Service -Name cloudflared -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq 'StopPending') {
    Get-Process -Name cloudflared -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}

try { Start-Service -Name cloudflared -ErrorAction Stop } catch {}
