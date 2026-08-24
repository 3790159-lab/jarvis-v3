# Native ops watchdog loop — the independent monitor that pings the backend /
# DEV-17 ops endpoints and Telegram-alerts Daniil directly when something is
# DOWN. Registered as the JarvisOpsWatchdog scheduled task (S4U / RunLevel
# Highest, AtStartup) exactly like JarvisBotGuardian / JarvisBackendGuardian, so
# it keeps watching across logoff, SSH drops, the starting session, AND a fully
# unattended headless reboot (the 03:14-backend-death scenario that motivated it).
#
# This is a DELIBERATELY THIN wrapper: every decision (which endpoints, debounce,
# dedup, recovery, alert text, TG send) lives in the stdlib-only, unit-tested
# scripts\ops_watchdog.py, which it invokes once per cycle. Mirrors the way the
# bot guardian shells out to boot_watch_check.py / regress_watch_check.py.
#
#   * Single-instance (P4-style) PID lockfile — a second watchdog exits.
#   * Own liveness stamp (state\ops_watchdog_heartbeat.txt) each cycle.
#   * PYTHONUTF8=1 prevents the cp1251 emoji crash (memory jarvis-detached-bot-utf8)
#     — the alerts contain 🚨 / ✅, so this is load-bearing, not cosmetic.
#   * Write-G uses Add-Content + Write-Host (never Tee-Object) so a logging call
#     never leaks strings into a function's return value.

param(
    [int]$IntervalSeconds = 30,
    [string]$Root = 'C:\jarvis',
    # Run exactly one probe cycle and exit (manual / live-test use, e.g. the
    # kill-backend drill) — skips the single-instance lock and the infinite loop.
    # The production task (register_ops_watchdog.ps1) does NOT pass this.
    [switch]$Once
)

$ErrorActionPreference = 'Continue'
Set-Location $Root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

# DEV-59: declare the console encoding as UTF-8 -- the READER side, and the
# other end of the pipe from PYTHONUTF8 just above (the WRITER side). Both
# stay: dropping either one closes a path with nothing.
# This file is the ONE place in the park where the declaration actually
# reaches the output (spec DEV-59, section 7): `& $py ... 2>&1 | ForEach-Object`
# below runs the child THROUGH this console, unlike the Start-Process
# redirects the other guardians use, which have no console in the chain at all.
# Measured 24.08 with a probe task: under Task Scheduler the console starts in
# cp866 and this setter does not throw even with no console attached.
# Stands before the first print (Write-G below).
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$watchScript = Join-Path $Root 'scripts\ops_watchdog.py'
$logDir      = Join-Path $Root 'state\logs'
$lockDir     = Join-Path $Root 'state\locks'
New-Item -ItemType Directory -Force -Path $logDir, $lockDir | Out-Null
$lockFile    = Join-Path $lockDir 'ops_watchdog.pid'
$gOut        = Join-Path $logDir 'ops_watchdog.stdout.log'
$gHbFile     = Join-Path $Root 'state\ops_watchdog_heartbeat.txt'

function Write-G([string]$msg) {
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Add-Content -LiteralPath $gOut -Value $line
    Write-Host $line
}

function Write-WatchdogBeat {
    [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() |
        Out-File -FilePath $gHbFile -Encoding ascii -Force
}

function Invoke-Cycle {
    Write-WatchdogBeat
    try { & $py $watchScript 2>&1 | ForEach-Object { Write-G "py: $_" } }
    catch { Write-G "cycle error: $_" }
}

if ($Once) {
    Write-G 'ops watchdog: single cycle (-Once)'
    Invoke-Cycle
    return
}

# ---- single-instance guard (P4-style PID lock) -----------------------------
if (Test-Path $lockFile) {
    $old = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($old) {
        $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
        if ($alive -and $alive.ProcessName -match 'powershell|pwsh') {
            Write-G "another ops watchdog already running (PID $old) - exiting"
            return
        }
    }
}
$PID | Out-File -FilePath $lockFile -Encoding ascii -Force
Write-G "ops watchdog started (PID $PID), probing every ${IntervalSeconds}s -> $watchScript"

while ($true) {
    Invoke-Cycle
    Start-Sleep -Seconds $IntervalSeconds
}
