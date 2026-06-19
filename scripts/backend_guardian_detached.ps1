# Keeps the Jarvis FastAPI backend (port 8010) alive, fully detached from any
# interactive/SSH session. Invoked by the JarvisBackendGuardian scheduled task
# (S4U / RunLevel Highest, like JarvisSniperDetached) so it survives logoff,
# SSH drops and the session that started it.
#
# Design:
#   * Single-instance (P4-style): a PID lockfile stops a second guardian.
#   * Backend single-instance: stop_port_owner frees :8010 before every
#     (re)start, so a wedged/half-started uvicorn can never pile up duplicates.
#   * Clean start (P1+P2): launches scripts\run_backend_detached.py, which
#     loads .env + .env.runpod via env_bootstrap. NO secrets injected here.
#   * PYTHONUTF8=1 prevents the cp1251 emoji crash (memory jarvis-detached-bot-utf8).

param(
    [int]$Port = 8010,
    [int]$IntervalSeconds = 15
)

$ErrorActionPreference = 'Continue'
$Root = 'C:\jarvis'
Set-Location $Root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py        = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$entry     = Join-Path $Root 'scripts\run_backend_detached.py'
$logDir    = Join-Path $Root 'state\logs'
$lockDir   = Join-Path $Root 'state\locks'
New-Item -ItemType Directory -Force -Path $logDir, $lockDir | Out-Null
$lockFile  = Join-Path $lockDir 'backend_guardian.pid'
$gOut      = Join-Path $logDir 'backend_guardian.stdout.log'
$bOut      = Join-Path $logDir 'backend_boot.stdout.log'
$bErr      = Join-Path $logDir 'backend_boot.stderr.log'

function Write-G([string]$msg) {
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    $line | Tee-Object -FilePath $gOut -Append
}

# ---- single-instance guard (P4-style PID lock) -----------------------------
if (Test-Path $lockFile) {
    $old = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($old) {
        $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
        if ($alive -and $alive.ProcessName -match 'powershell|pwsh') {
            Write-G "another guardian already running (PID $old) - exiting"
            return
        }
    }
}
$PID | Out-File -FilePath $lockFile -Encoding ascii -Force
Write-G "guardian started (PID $PID), watching :$Port every ${IntervalSeconds}s"

function Test-Backend {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        return $true
    } catch { return $false }
}

function Start-Backend {
    # Backend single-instance: free the port first so we never run two.
    & (Join-Path $Root 'scripts\stop_port_owner.ps1') -Port $Port *>> $gOut
    Start-Sleep -Seconds 2
    $p = Start-Process -FilePath $py -ArgumentList @($entry) -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $bOut -RedirectStandardError $bErr -PassThru
    Write-G "launched backend (PID $($p.Id)) -> $bOut"
    # Wait for readiness so the steady-state loop doesn't kill a starting backend.
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Backend) { Write-G "backend healthy after ~${i}s"; return $true }
    }
    Write-G "backend NOT healthy after 40s - will retry next cycle"
    return $false
}

$lastState = ''
while ($true) {
    if (Test-Backend) {
        if ($lastState -ne 'alive') { Write-G 'backend alive'; $lastState = 'alive' }
    } else {
        if ($lastState -ne 'dead') { Write-G 'backend DOWN - restarting'; $lastState = 'dead' }
        Start-Backend | Out-Null
        if (Test-Backend) { $lastState = 'alive' }
    }
    Start-Sleep -Seconds $IntervalSeconds
}
