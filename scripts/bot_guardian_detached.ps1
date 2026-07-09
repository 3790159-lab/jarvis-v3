# Keeps the Jarvis Telegram bot alive, fully detached from any interactive/SSH
# session. Invoked by the JarvisBotGuardian scheduled task (S4U / RunLevel
# Highest, like JarvisBackendGuardian / JarvisSniperDetached) so it survives
# logoff, SSH drops, the starting session, and a reboot (AtStartup trigger) —
# crucially WITHOUT needing an interactive desktop logon.
#
# Design (mirrors backend_guardian_detached.ps1):
#   * Single-instance (P4-style): a PID lockfile stops a second guardian.
#   * Liveness via the bot's own heartbeat file (state\bot_heartbeat.txt, the
#     bot rewrites it every 30s) — catches a hung bot, not just a dead PID.
#   * Bot single-instance: Stop-OldBot clears any stale/hung bot + its pid file
#     before launch, so the bot's own state\bot.pid guard never blocks recovery.
#   * Clean start (P1+P2): the bot self-loads .env + .env.runpod via
#     `from app import env_bootstrap`, so NO secrets are injected here.
#   * PYTHONUTF8=1 prevents the cp1251 emoji crash (memory jarvis-detached-bot-utf8).

param(
    [int]$IntervalSeconds = 30,
    [int]$HeartbeatMaxAgeSec = 90
)

$ErrorActionPreference = 'Continue'
$Root = 'C:\jarvis'
Set-Location $Root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py        = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$botFile   = Join-Path $Root 'tools\jarvis_smart_telegram_control.py'
$logDir    = Join-Path $Root 'state\logs'
$lockDir   = Join-Path $Root 'state\locks'
New-Item -ItemType Directory -Force -Path $logDir, $lockDir | Out-Null
$lockFile  = Join-Path $lockDir 'bot_guardian.pid'
$gOut      = Join-Path $logDir 'bot_guardian.stdout.log'
$bOut      = Join-Path $logDir 'bot_boot.stdout.log'
$bErr      = Join-Path $logDir 'bot_boot.stderr.log'
$hbFile    = Join-Path $Root 'state\bot_heartbeat.txt'
$gHbFile   = Join-Path $Root 'state\guardian_heartbeat.txt'
$botPid    = Join-Path $Root 'state\bot.pid'
$regressWatch      = Join-Path $Root 'state\regress_watch.json'
$regressWatchCheck = Join-Path $Root 'scripts\regress_watch_check.py'

function Write-G([string]$msg) {
    $line = ('{0} | {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    $line | Tee-Object -FilePath $gOut -Append
}

function Write-GuardianBeat {
    # Own liveness stamp (unix UTC secs), re-written every loop cycle so /health
    # can tell a *live* нянька from one that silently hung — matching how the bot
    # proves itself via state\bot_heartbeat.txt.
    [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() |
        Out-File -FilePath $gHbFile -Encoding ascii -Force
}

# ---- single-instance guard (P4-style PID lock) -----------------------------
if (Test-Path $lockFile) {
    $old = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($old) {
        $alive = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
        if ($alive -and $alive.ProcessName -match 'powershell|pwsh') {
            Write-G "another bot guardian already running (PID $old) - exiting"
            return
        }
    }
}
$PID | Out-File -FilePath $lockFile -Encoding ascii -Force
Write-GuardianBeat
Write-G "bot guardian started (PID $PID), heartbeat<=${HeartbeatMaxAgeSec}s every ${IntervalSeconds}s"

function Test-Bot {
    # A bot process MUST exist. The heartbeat file persists after the bot dies
    # (or across a reboot), so freshness alone would falsely report "alive" —
    # require the process first, then use the heartbeat to also catch a hung one.
    $proc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'jarvis_smart_telegram_control' }
    if (-not $proc) { return $false }
    if (-not (Test-Path $hbFile)) { return $false }  # just launched, not ready yet
    try {
        $last = [int64]((Get-Content $hbFile -ErrorAction Stop | Select-Object -First 1).Trim())
        $now  = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        return (($now - $last) -le $HeartbeatMaxAgeSec)
    } catch { return $false }
}

function Stop-OldBot {
    if (Test-Path $botPid) {
        try {
            $op = [int]((Get-Content $botPid -ErrorAction Stop) -join '').Trim()
            if (Get-Process -Id $op -ErrorAction SilentlyContinue) {
                Stop-Process -Id $op -Force -ErrorAction SilentlyContinue
                Write-G "stopped stale bot PID $op (pid file)"
            }
        } catch {}
    }
    try {
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match 'jarvis_smart_telegram_control' } |
            ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                Write-G "stopped bot proc $($_.ProcessId) (cmdline)"
            }
    } catch {}
    if (Test-Path $botPid) { Remove-Item $botPid -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 700
}

function Start-Bot {
    Stop-OldBot
    $p = Start-Process -FilePath $py -ArgumentList @($botFile) -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput $bOut -RedirectStandardError $bErr -PassThru
    Write-G "launched bot (PID $($p.Id)) -> $bOut"
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Bot) { Write-G "bot heartbeat fresh after ~${i}s"; return $true }
    }
    Write-G "bot heartbeat NOT fresh after 45s - will retry next cycle"
    return $false
}

$lastState = ''
while ($true) {
    Write-GuardianBeat   # prove the нянька is awake every cycle, before any work

    # Detached regress watchdog, layer 2 (Этап 1, хвост #6): if a regress marker
    # exists, run the standalone stdlib-only killer. It no-ops for a healthy run;
    # if the regress is past its deadline OR orphaned (its parent bot PID dead),
    # it taskkills the PID-group, TG-alerts the admin, and clears the marker —
    # the case the bot's own loop can't cover because the bot itself has died.
    # Cheap: only spawns Python when the marker is actually present.
    if (Test-Path $regressWatch) {
        try { & $py $regressWatchCheck 2>$null } catch {}
    }

    if (Test-Bot) {
        if ($lastState -ne 'alive') { Write-G 'bot alive'; $lastState = 'alive' }
    } else {
        if ($lastState -ne 'dead') { Write-G 'bot DOWN - restarting'; $lastState = 'dead' }
        Start-Bot | Out-Null
        if (Test-Bot) {
            $lastState = 'alive'
        } else {
            # Dev-task (Ступень 2) crash-loop guard: if a [Мердж] wrote a
            # boot_watch marker and the merged code never boots healthy past its
            # deadline, this standalone stdlib-only script TG-alerts the admin
            # with rollback commands. Survives even a merge that breaks the bot's
            # imports. No-op when no marker / bot already healthy.
            try { & $py (Join-Path $Root 'scripts\boot_watch_check.py') 2>$null } catch {}
        }
    }
    Start-Sleep -Seconds $IntervalSeconds
}
