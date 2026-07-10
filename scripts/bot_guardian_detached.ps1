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
    # Раньше 90с; RAM-штормы, которые вызывали транзиентные heartbeat-стойлы,
    # починены батчами регресса (Этап 1, хвост #4) - поднимаем порог, чтобы
    # разовый GC/CPU-стол не читался как смерть бота. Debounce ниже - вторая
    # линия защиты от того же класса ложных срабатываний.
    [int]$HeartbeatMaxAgeSec = 180,
    # Слой C: relaunch только после N подряд неудачных проверок, а не с первой.
    [int]$DebounceFailures = 3,
    # Параметризовано для Python-интеграционных тестов (фейковый долгоживущий
    # процесс + временный $Root) - прод не передаёт этот флаг, дефолт не меняется.
    [string]$Root = 'C:\jarvis',
    # Тестовый хук: только определить функции (Test-Bot/Stop-OldBot/Start-Bot),
    # не брать single-instance lock и не входить в бесконечный цикл. Прод-вызов
    # (register_bot_guardian.ps1) этот флаг не передаёт - поведение не меняется.
    [switch]$NoLoop
)

$ErrorActionPreference = 'Continue'
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
    # Persist to the durable log file AND echo to the console, but NEVER emit to
    # the PowerShell success (output) stream. The old `Tee-Object -FilePath`
    # passed every line THROUGH to the pipeline, so a function that called Write-G
    # (Stop-OldBot / Start-Bot) returned an ARRAY of log strings + its real value.
    # `if (-not (Stop-OldBot))` then saw a truthy array and NEVER aborted → the
    # guardian launched a SECOND bot on top of a live one, defeating Layer B's core
    # "never start on top of a live poller" guarantee (caught by test_start_bot_
    # aborts_launch_when_old_bot_wont_die). Add-Content + Write-Host keep both the
    # file log and console visibility with a clean, side-effect-free return.
    Add-Content -LiteralPath $gOut -Value $line
    Write-Host $line
}

function Write-GuardianBeat {
    # Own liveness stamp (unix UTC secs), re-written every loop cycle so /health
    # can tell a *live* нянька from one that silently hung — matching how the bot
    # proves itself via state\bot_heartbeat.txt.
    [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() |
        Out-File -FilePath $gHbFile -Encoding ascii -Force
}

# ---- single-instance guard (P4-style PID lock) -----------------------------
# Skipped under -NoLoop: test harness dot-sources this file to reach the
# functions below without taking the real guardian's lock.
if (-not $NoLoop) {
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
    Write-G "bot guardian started (PID $PID), heartbeat<=${HeartbeatMaxAgeSec}s every ${IntervalSeconds}s, debounce=${DebounceFailures}"
}

function Get-BotProcesses {
    # Single source of truth for "what counts as a live bot process", shared by
    # Test-Bot (liveness) and Stop-OldBot (kill target + death poll).
    #
    # INSTANCE-SCOPED to $Root (not a global 'jarvis_smart_telegram_control'
    # substring): the bot is always launched as "$py $botFile" with both paths
    # under $Root, so its cmdline necessarily contains $Root. Scoping by $Root
    # is what makes this hermetically testable AND prod-safe — a Python-integration
    # test running with a temp $Root can never match (let alone taskkill) a REAL
    # bot living under C:\jarvis, and a second bot under a different root can't be
    # mistaken for ours. -like (not -match) so the backslashes in the path are
    # treated literally, not as regex.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Root*jarvis_smart_telegram_control*" }
}

function Test-Bot {
    # A bot process MUST exist. The heartbeat file persists after the bot dies
    # (or across a reboot), so freshness alone would falsely report "alive" —
    # require the process first, then use the heartbeat to also catch a hung one.
    $proc = Get-BotProcesses
    if (-not $proc) { return $false }
    if (-not (Test-Path $hbFile)) { return $false }  # just launched, not ready yet
    try {
        $last = [int64]((Get-Content $hbFile -ErrorAction Stop | Select-Object -First 1).Trim())
        $now  = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        return (($now - $last) -le $HeartbeatMaxAgeSec)
    } catch { return $false }
}

function Stop-OldBot {
    # Layer B: kill by PID-from-file AND by cmdline match, both as a full
    # process TREE (taskkill /T), then POLL for confirmed death up to
    # $MaxWaitSec before deciding. bot.pid is only removed once nothing
    # matching the bot is left alive - if a process survives past the
    # deadline, this returns $false and leaves bot.pid in place so Start-Bot
    # (below) refuses to launch a second poller on top of a live one.
    param([int]$MaxWaitSec = 10)

    $opPid = $null
    if (Test-Path $botPid) {
        try {
            $opPid = [int]((Get-Content $botPid -ErrorAction Stop) -join '').Trim()
            if (Get-Process -Id $opPid -ErrorAction SilentlyContinue) {
                & taskkill.exe /PID $opPid /T /F *> $null
                Write-G "taskkill sent to stale bot PID $opPid (pid file, +tree)"
            }
        } catch { $opPid = $null }
    }
    Get-BotProcesses | ForEach-Object {
        & taskkill.exe /PID $_.ProcessId /T /F *> $null
        Write-G "taskkill sent to bot proc $($_.ProcessId) (cmdline, +tree)"
    }

    function Test-BotStillAlive {
        if ($opPid -and (Get-Process -Id $opPid -ErrorAction SilentlyContinue)) { return $true }
        if (Get-BotProcesses) { return $true }
        return $false
    }

    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Test-BotStillAlive) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }

    if (Test-BotStillAlive) {
        Write-G "Stop-OldBot: bot still alive after ${MaxWaitSec}s - NOT clearing pid, NOT starting new (retry next cycle)"
        return $false
    }

    if (Test-Path $botPid) { Remove-Item $botPid -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 700
    return $true
}

function Start-Bot {
    if (-not (Stop-OldBot)) {
        Write-G "Start-Bot: old bot still alive - aborting launch (never start on top of a live poller)"
        return $false
    }
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

if (-not $NoLoop) {
    $lastState = ''
    $consecutiveFail = 0   # Layer C: consecutive failed Test-Bot checks
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
            $consecutiveFail = 0
            if ($lastState -ne 'alive') { Write-G 'bot alive'; $lastState = 'alive' }
        } else {
            $consecutiveFail++
            if ($consecutiveFail -lt $DebounceFailures) {
                Write-G "bot check failed (${consecutiveFail}/${DebounceFailures}) - debouncing, not relaunching yet"
            } else {
                if ($lastState -ne 'dead') { Write-G 'bot DOWN - restarting'; $lastState = 'dead' }
                $started = Start-Bot
                if ($started -and (Test-Bot)) {
                    $lastState = 'alive'
                    $consecutiveFail = 0
                } else {
                    # Dev-task (Ступень 2) crash-loop guard: if a [Мердж] wrote a
                    # boot_watch marker and the merged code never boots healthy past its
                    # deadline, this standalone stdlib-only script TG-alerts the admin
                    # with rollback commands. Survives even a merge that breaks the bot's
                    # imports. No-op when no marker / bot already healthy.
                    try { & $py (Join-Path $Root 'scripts\boot_watch_check.py') 2>$null } catch {}
                }
            }
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}
