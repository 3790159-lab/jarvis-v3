# Keeps the chatter Telethon userbot (chatter.telethon_run) alive, fully detached
# from any interactive/SSH session. Invoked by the JarvisChatterGuardian scheduled
# task (S4U / RunLevel Highest, like JarvisBotGuardian) so it survives logoff, SSH
# drops, the starting session, and a reboot (AtStartup) WITHOUT an interactive logon.
#
# Design mirrors bot_guardian_detached.ps1:
#   * Single-instance: a PID lockfile stops a second guardian.
#   * Liveness via the runner's own heartbeat file (state\chatter_heartbeat.txt,
#     rewritten every 30s) AND process existence — catches a hung runner, not just
#     a dead PID.
#   * Runner single-instance: Stop-OldRunner taskkills any stale/hung runner
#     (cmdline-matched, whole tree) before launch, so we never end up with the
#     6-instances-fighting-over-the-session failure the operator hit by hand.
#   * Clean start: the runner self-loads .env (TELEGRAM_API_ID/HASH, ANTHROPIC_API_KEY)
#     via its own .env fallback, so NO secrets are injected here.
#   * On DOWN: fire chatter_watch_check.py (stdlib-only) to TG-alert the operator.
#   * PYTHONUTF8=1 prevents the cp1251 emoji crash (memory jarvis-detached-bot-utf8).

param(
    [int]$IntervalSeconds = 30,
    [int]$HeartbeatMaxAgeSec = 180,   # tolerate a one-off GC/CPU stall (debounce is the 2nd line)
    [int]$DebounceFailures = 3,       # relaunch only after N consecutive failed checks
    [string]$Root = 'C:\jarvis',
    [switch]$NoLoop                    # test hook: define functions only, no lock, no loop
)

$ErrorActionPreference = 'Continue'
Set-Location $Root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py        = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$logDir    = Join-Path $Root 'logs'
$stateDir  = Join-Path $Root 'state'
$lockDir   = Join-Path $Root 'state\locks'
New-Item -ItemType Directory -Force -Path $logDir, $stateDir, $lockDir | Out-Null
$lockFile  = Join-Path $lockDir 'chatter_guardian.pid'
$gOut      = Join-Path $logDir 'chatter_guardian.stdout.log'
$rOut      = Join-Path $logDir 'chatter_telethon.log'          # same runner log as the manual launch
$hbFile    = Join-Path $stateDir 'chatter_heartbeat.txt'
$gHbFile   = Join-Path $stateDir 'chatter_guardian_heartbeat.txt'
$watchCheck = Join-Path $Root 'scripts\chatter_watch_check.py'

function Write-G([string]$msg) {
    # Add-Content + Write-Host (NOT Tee-Object): keep a side-effect-free return so
    # callers using `if (-not (Stop-OldRunner))` see a real boolean, not a log array.
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Add-Content -LiteralPath $gOut -Value $line
    Write-Host $line
}

function Write-GuardianBeat {
    [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() |
        Out-File -FilePath $gHbFile -Encoding ascii -Force
}

# ---- single-instance guard (PID lock) --------------------------------------
if (-not $NoLoop) {
    if (Test-Path $lockFile) {
        $old = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($old) {
            $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
            if ($alive -and $alive.ProcessName -match 'powershell|pwsh') {
                Write-G "another chatter guardian already running (PID $old) - exiting"
                return
            }
        }
    }
    $PID | Out-File -FilePath $lockFile -Encoding ascii -Force
    Write-GuardianBeat
    Write-G "chatter guardian started (PID $PID), heartbeat<=${HeartbeatMaxAgeSec}s every ${IntervalSeconds}s, debounce=${DebounceFailures}"
}

function Get-RunnerProcesses {
    # INSTANCE-SCOPED to $Root: the runner is always launched as "$py -m
    # chatter.telethon_run" with the venv under $Root, so its cmdline contains
    # both $Root and the module. -like (not -match) so backslashes stay literal.
    # NOTE: on Windows the venv Scripts\python.exe re-execs the base interpreter,
    # so a healthy runner shows as TWO matching processes (launcher + worker) —
    # that is expected and fine; we only need >=1 alive plus a fresh heartbeat.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Root*chatter.telethon_run*" }
}

function Test-Runner {
    # A runner process MUST exist AND its heartbeat must be fresh. The heartbeat
    # file persists after death / across reboot, so freshness alone would falsely
    # report alive — require the process first.
    $proc = Get-RunnerProcesses
    if (-not $proc) { return $false }
    if (-not (Test-Path $hbFile)) { return $false }  # just launched, not ready yet
    try {
        $last = [int64]((Get-Content $hbFile -ErrorAction Stop | Select-Object -First 1).Trim())
        $now  = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        return (($now - $last) -le $HeartbeatMaxAgeSec)
    } catch { return $false }
}

function Stop-OldRunner {
    # Kill every matching runner process as a full tree (taskkill /T), then POLL
    # for confirmed death. Returns $false if anything survives, so Start-Runner
    # refuses to launch a second userbot on top of a live session.
    param([int]$MaxWaitSec = 10)

    Get-RunnerProcesses | ForEach-Object {
        & taskkill.exe /PID $_.ProcessId /T /F *> $null
        Write-G "taskkill sent to runner proc $($_.ProcessId) (cmdline, +tree)"
    }
    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Get-RunnerProcesses) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    if (Get-RunnerProcesses) {
        Write-G "Stop-OldRunner: runner still alive after ${MaxWaitSec}s - NOT starting new (retry next cycle)"
        return $false
    }
    Start-Sleep -Milliseconds 700
    return $true
}

function Start-Runner {
    if (-not (Stop-OldRunner)) {
        Write-G "Start-Runner: old runner still alive - aborting launch (never start on top of a live session)"
        return $false
    }
    $runnerArgs = @('-u', '-m', 'chatter.telethon_run', '--llm', 'real')
    $p = Start-Process -FilePath $py -ArgumentList $runnerArgs -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $rOut -RedirectStandardError $rOut -PassThru
    Write-G "launched chatter runner (PID $($p.Id)) -> $rOut"
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Runner) { Write-G "runner heartbeat fresh after ~${i}s"; return $true }
    }
    Write-G "runner heartbeat NOT fresh after 45s - will retry next cycle"
    return $false
}

if (-not $NoLoop) {
    $lastState = ''
    $consecutiveFail = 0
    while ($true) {
        Write-GuardianBeat

        if (Test-Runner) {
            $consecutiveFail = 0
            if ($lastState -ne 'alive') { Write-G 'runner alive'; $lastState = 'alive' }
        } else {
            $consecutiveFail++
            if ($consecutiveFail -lt $DebounceFailures) {
                Write-G "runner check failed (${consecutiveFail}/${DebounceFailures}) - debouncing, not relaunching yet"
            } else {
                if ($lastState -ne 'dead') { Write-G 'runner DOWN - restarting'; $lastState = 'dead' }
                # TG-alert the operator (stdlib-only, self-dedups via cooldown) BEFORE
                # relaunch, so a crash is visible even if recovery also fails.
                try { & $py $watchCheck 2>$null } catch {}
                $started = Start-Runner
                if ($started -and (Test-Runner)) {
                    $lastState = 'alive'
                    $consecutiveFail = 0
                }
            }
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}
